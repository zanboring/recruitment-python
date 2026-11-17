"""URL 安全校验：阻断 SSRF（服务端请求伪造）。

**要解决什么**
`POST /api/jobs/url-import` 允许用户粘贴任意链接，由**服务端**替他去请求。
若只做 `url.startswith(("http://", "https://"))`，用户就能填
`http://127.0.0.1:8080/api/...`、`http://192.168.1.1/`、
`http://169.254.169.254/latest/meta-data/`（云厂商元数据），
让服务端带着内网身份去访问 —— 这是标准 SSRF。

**为什么不能靠字符串前缀 / 正则**
- `http://127.0.0.1.evil.com/` 前缀看着像回环，实际是公网域名；
- 回环有大量等价写法：`http://2130706433/`（十进制）、`http://0x7f000001/`（十六进制）、
  `http://[::1]/`、`http://[::ffff:127.0.0.1]/`、`http://127.1/`。
所以必须先**归一化成 IP**，再用 `ipaddress` 的语义判断（见 `_is_forbidden_ip`）。

**为什么必须在「解析之后」判断**
`http://internal.example.com` 可能解析到 `10.x`，只看 URL 字符串永远看不出来。
本模块对**所有**解析结果做判断，且任一结果为内网即拒绝（fail closed）。

**为什么重定向要「自己跟」**
`httpx` 的 `follow_redirects=True` 会在**请求已经发出去之后**才给出最终 URL ——
那时内网已经被访问过了，事后校验拦不住任何东西。
因此调用方需 `follow_redirects=False` 并逐跳调用本模块校验（见 `_fetch_page_via_http`）。

**残余风险（如实记录，2026-11-08 更新）**
校验与真正建连之间会各解析一次域名，存在 DNS rebinding（TOCTOU）窗口：
第一次解析判为公网、第二次解析落到 `127.0.0.1`，闸门就形同虚设。
彻底消除需要把解析结果钉到连接层（自定义 transport：直连已验证 IP + 保留 Host 头）。
**当前没有做这件事** —— 全项目 grep `transport=` / `local_addr` / `sock=` 均 0 命中。

调用方有两类，暴露面完全不同，不要混为一谈：

1. **链接导入 / 存活核查**：URL 是**管理员手动粘贴**的，攻击者无法直接构造目标；
2. **`SpecCrawler`**（2026-11-08 接入）：URL 来自**被爬页面里的 `href`**，
   攻击者只要控制那个页面就能构造目标。**这一类的 rebinding 窗口是已知未修风险**，
   现有缓解只有「同站抓取 + 域名节流」，那两条是**礼貌措施，不是安全边界**。

> 之前这里写的是「本项目暴露面是管理员手动粘贴链接，当前实现已覆盖实际风险」——
> 那句在爬虫接入之后就不成立了。留着会让人误以为这条路径是安全的。

"""
import asyncio
import ipaddress
import logging
import socket
from urllib.parse import urljoin, urlsplit

logger = logging.getLogger(__name__)

ALLOWED_SCHEMES = frozenset({"http", "https"})
_DEFAULT_PORTS = {"http": 80, "https": 443}


class UnsafeURLError(RuntimeError):
    """目标地址不允许服务端访问（内网 / 本机 / 非法 scheme / 解析失败）。

    继承 RuntimeError：调用方（路由层）已把 RuntimeError 映射为 HTTP 400，
    因此拒绝时自动得到 4xx + 明确原因，无需额外改造错误通道。
    """


def _is_forbidden_ip(ip) -> bool:
    """私有 / 回环 / 链路本地 / 保留 / 组播 / 未指定地址一律拒绝。"""
    # ::ffff:127.0.0.1 这类 IPv4 映射地址按 IPv4 判断，避免漏网
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    # `is_global` = 「全球可路由」，一条即可覆盖私有/回环/链路本地/保留/文档段
    # 以及 RFC 6598 的 100.64/10（CGNAT，`is_private` 并不包含它）。
    # 后面几个显式判据保留作双保险：既满足「用语义判断而非字符串前缀」的要求，
    # 也在 is_global 语义变化时仍能挡住最常见的内网地址。
    return bool(
        (not ip.is_global)
        or ip.is_private          # 10/8、172.16/12、192.168/16
        or ip.is_loopback         # 127/8、::1
        or ip.is_link_local       # 169.254/16（含云元数据 169.254.169.254）、fe80::/10
        or ip.is_reserved         # 240/4、::/8 等
        or ip.is_multicast        # 224/4、ff00::/8
        or ip.is_unspecified      # 0.0.0.0、::
    )


def _resolve(host: str, port: int) -> list:
    """解析主机名，返回去重后的 IP 字符串列表；解析失败即视为不安全。"""
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError) as e:
        raise UnsafeURLError(f"域名无法解析，已拒绝：{host}") from e
    addrs = []
    for info in infos:
        addr = str(info[4][0]).split("%", 1)[0]  # 去掉 IPv6 scope id（fe80::1%eth0）
        if addr not in addrs:
            addrs.append(addr)
    if not addrs:
        raise UnsafeURLError(f"域名未解析出任何地址，已拒绝：{host}")
    return addrs


def assert_safe_url(url: str) -> str:
    """校验 URL 可安全请求；不安全抛 `UnsafeURLError`，安全则返回原 URL（已 strip）。

    校验顺序：**scheme → 主机名 → 端口 → DNS 解析结果逐 IP 判断**。
    注意这里会真的做一次 DNS 解析（同步）；在异步请求路径里请用
    `assert_safe_url_async`，避免阻塞事件循环。
    """
    if not isinstance(url, str) or not url.strip():
        raise UnsafeURLError("请输入完整的 http/https 链接")
    raw = url.strip()

    try:
        parts = urlsplit(raw)
    except ValueError as e:
        raise UnsafeURLError(f"链接格式非法：{e}") from e

    scheme = (parts.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        shown = parts.scheme or "(空)"
        raise UnsafeURLError(f"请输入合法的 http/https 链接（当前 scheme：{shown}）")

    host = parts.hostname
    if not host:
        raise UnsafeURLError("链接缺少主机名")

    try:
        port = parts.port or _DEFAULT_PORTS[scheme]
    except ValueError as e:  # 端口非数字 / 越界
        raise UnsafeURLError(f"链接端口非法：{e}") from e

    for ip_str in _resolve(host, port):
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError as e:
            raise UnsafeURLError(f"解析结果不是合法 IP：{ip_str}") from e
        if _is_forbidden_ip(ip):
            logger.warning("拒绝 SSRF 目标：%s 解析到内网地址 %s", host, ip_str)
            raise UnsafeURLError(f"拒绝访问内网/本机地址：{host} -> {ip_str}")

    return raw


async def assert_safe_url_async(url: str) -> str:
    """`assert_safe_url` 的异步包装：把阻塞的 DNS 解析丢到线程池，避免卡住事件循环。"""
    return await asyncio.to_thread(assert_safe_url, url)


# ---------------------------------------------------------------------------
# 带防护的 HTTP GET —— 所有「替用户/替系统去请求一个外部 URL」的地方都该走这里
# ---------------------------------------------------------------------------

REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
MAX_REDIRECTS = 3


def redirect_location(resp) -> str:
    """取 3xx 的 Location；用 getattr 兼容测试里的极简响应替身。"""
    status = getattr(resp, "status_code", 0)
    if status not in REDIRECT_CODES:
        return ""
    headers = getattr(resp, "headers", None)
    if not headers:
        return ""
    try:
        return headers.get("location") or headers.get("Location") or ""
    except AttributeError:  # 非 Mapping 的替身
        return ""


async def safe_get(url: str, *, headers=None, timeout: float = 30.0):
    """**逐跳校验后**跟随重定向的安全 GET，返回最终响应。

    内部**强制** `follow_redirects=False`：这个开关交给调用方设置迟早会漏一处，
    而自动跟随会在请求已经打到内网之后才给出最终 URL —— 那时再校验等于没校验。

    安全性由 `assert_safe_url_async` 保证：每一跳在**发出请求之前**都做
    scheme 白名单 + DNS 解析后判内网。

    参数：
        url      目标地址（http/https）
        headers  请求头
        timeout  秒

    异常：不安全目标抛 `UnsafeURLError`。**由调用方决定语义** ——
    API 层映射成 400，后台任务（如存活核查）记 warning 后跳过。

    返回的响应体已完整读入（`client.get` 非 stream 模式），
    因此在客户端关闭后继续读 `resp.text` / `resp.status_code` 是安全的。

    **代理取舍（2026-11-17，抗非法代理环境）**：构造客户端时 ``trust_env=False``，
    不再读环境里的 ``HTTP_PROXY`` / ``HTTPS_PROXY`` / ``NO_PROXY``。原因：httpx
    0.27.0 解析 ``NO_PROXY`` 时，带括号的 IPv6（如 ``[::1]``，Windows 常见默认值）
    不被识别为 IPv6，落进通配域名分支拼出 ``all://*[::1]``，``URLPattern`` 把
    ``:1]`` 当端口 → **构造客户端即抛 ``InvalidURL``** —— 请求还没发、SSRF 闸门
    还没跑，整条出站就崩了，且错误形状像 URL 问题，排查会往 URL 上找。环境代理
    是全局、可被任意进程污染、格式不受我们控制的变量，不该让一条安全出站路径的
    可用性押在它上面。

    需要代理时改用项目已有的**显式**配置 ``settings.crawl_proxy``（与浏览器采集
    同一旋钮，见 ``app/crawlers/browser.py``）：由管理员配置、格式可控，设了就走它，
    没设就直连。代价：依赖环境 ``HTTP_PROXY`` 的部署不再被本路径自动读取 —— 这是
    刻意的，把「隐式、会崩」换成「显式、可控」。``crawl_proxy`` 必须是合法代理 URL
    （``http://host:port`` 或 ``http://user:pass@host:port``）。

    安全性不变：每一跳仍在**发出请求之前**做 scheme 白名单 + DNS 解析后判内网，
    ``follow_redirects=False`` 不变。走代理时由代理端建连，DNS rebinding 残余风险
    与直连路径同类（见模块 docstring），不新增绕过闸门的通道。
    """
    import httpx  # 延迟导入：本模块其余部分只依赖标准库
    from app.config import settings  # 延迟导入：取显式代理旋钮，避免模块级依赖 config

    proxy = settings.crawl_proxy or None
    current = url
    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=False, headers=headers or {},
        trust_env=False, proxy=proxy,
    ) as client:
        for _ in range(MAX_REDIRECTS + 1):
            current = await assert_safe_url_async(current)
            resp = await client.get(current)
            location = redirect_location(resp)
            if not location:
                return resp
            await resp.aclose()                   # 不读正文，及时释放连接
            current = urljoin(current, location)  # 兼容相对路径的 Location
    raise UnsafeURLError(f"重定向次数超过 {MAX_REDIRECTS} 次，已拒绝")

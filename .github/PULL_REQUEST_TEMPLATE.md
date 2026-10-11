## 这个 PR 做了什么

<!-- 一两句话说清改动内容与动机 -->

## 改动类型

- [ ] `feat` 新功能
- [ ] `fix` 缺陷修复
- [ ] `refactor` 重构（不改变外部行为）
- [ ] `perf` 性能
- [ ] `docs` 文档
- [ ] `test` 测试
- [ ] `chore` / `build` 杂项

## 自检清单

- [ ] 本地跑过 `pytest -q`，**全绿**（当前基线 772 passed）
- [ ] 没有提交 `.env` / `config.env` / `*.db` / 任何密钥
- [ ] `git add` 用的是显式文件路径，没有 `git add -A`
- [ ] 新增的 `Settings` 字段默认值指向本机真实存在的东西（避免静默降级）
- [ ] 新增的 `async` 调用没有漏 `await`（AST 守卫会拦，但请自己先看一眼）
- [ ] 若改了依赖 / Dockerfile / 前端，已本地跑过 `docker compose up -d --build`

## 风险与回退

<!-- 有什么副作用？出问题怎么退？没有就写「无」 -->

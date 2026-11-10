// Rosezm project config for the Windows Hotspot Butler port.
//
// 后端容器不在 config 钩子里启动：serve 的 onServe 走原生 Node dynamic import，
// 加载未打包的 .ts 模块会被 Node 26 拒绝（框架 BUG.md BN-5）。真正的启动点是
// api 路由里的 ensureBackend()（幂等、每 worker 一次，用的是已打包成纯 JS 的
// dist/server.js）。
//
// 构建期（prerender）不启动：由 rosezm-cli.mjs 注入 WHM_RUNTIME=1 区分运行/构建。
export default [];

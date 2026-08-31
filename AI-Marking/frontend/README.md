# 实验平台前端

React/Vite 前端提供 r20 v2 SAF 官方边界项目、锁定报告、提示词验证和模型配置页面。提示词页显示持久化 48-call validation suite 的状态与进度；项目页把每道题的 NM、CRM、ARM 显示为三个可单独启动的实验组，并保证同一时间只有一组运行。v1 项目显示为历史只读。项目和 suite 只在活动时轮询；调用内容在完成并锁定报告前不可读取。请求不使用通用 30 秒 Axios 超时，由后端冻结的模型超时和重试边界控制。

```bash
npm run lint
npm run test:run
npm run build
npm audit --omit=dev
```

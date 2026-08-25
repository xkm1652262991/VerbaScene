# VerbaScene Web

动画英语短剧工作台的 React 18 + TypeScript + Vite 前端。

## 本地开发

```bash
cd apps/web
npm install
npm run dev
```

默认地址为 `http://127.0.0.1:5173`，API 默认为 `http://127.0.0.1:8000`。覆盖 API 地址：

```bash
cp .env.example .env.local
```

## 当前页面

```text
#/projects                         项目列表与创建入口
#/projects/{project_id}            项目工作台
#/projects/{project_id}/chapter    项目剧本抽屉
#/projects/{project_id}/assets     项目工作台的资产视图
#/projects/{project_id}/assets/generate 资产图生成
#/projects/{project_id}/shots/{shot_id} 单片段编辑定位
#/tasks                            任务中心
#/models                           模型管理
```

项目内围绕剧本、资产库和视频制作组织，导出是视频制作中的操作，不是阻止导航的独立阶段。

## 合同与构建

```bash
npm run check:api-contract
npm run build
```

合同检查读取 `../../docs/api/openapi.json`，核对 `src/services/apiClient.ts` 使用的 API 路径。

## 当前边界

- 使用轻量 Hash 路由，没有引入独立路由框架。
- 页面读取 `/workbench` 聚合快照，剧本和视频长任务统一通过任务查询更新状态；活动任务支持取消，失败或取消任务支持人工重试。
- 生成时只锁定同一资源的重复提交，不锁定整个工作区。
- 项目视频批量生成按父子任务展示汇总与镜头级状态，前端不会把 `202` 受理响应误报为生成完成。
- 当前是内部单机工具界面，没有登录、权限和多租户 UI。

产品布局见 [前端工作台设计](../../docs/08-frontend-workbench.md)。

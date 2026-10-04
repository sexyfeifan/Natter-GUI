# 上游同步、构建与发布

## 固定源码

上游地址只允许 `MikeWang000000/Natter`。`upstream.lock.json` 保存正式 Release 标签、不可变的 commit SHA 和三个文件的 SHA-256。GUI 不修改这些文件。

`python3 scripts/sync_upstream.py --verify` 离线验证文件一致性。
`python3 scripts/sync_upstream.py --check` 查询是否有新正式 Release。
`python3 scripts/sync_upstream.py` 下载新版本，检查 Python 语法、CLI 参数与版本，然后更新核心目录和锁文件。

同步脚本先下载到临时目录并验证，再替换本地源码。GitHub 工作流随后执行完整测试，通过后才提交推送。上游更改关键参数、输出格式或版本信息时应阻止自动发布并修复适配器。

## 自动工作流

`sync-upstream.yml` 每天查询最新正式 Release，也支持手动运行。它只更新核心文件与锁文件。工作流显式请求 contents:write 和 packages:write；组织策略或 main 分支保护可能阻止写入，此时任务失败而不强制绕过保护。

`GITHUB_TOKEN` 推送通常不会触发普通 push 工作流，因此同步完成后显式调用可复用的 `image.yml`，以刚提交的 SHA 构建。[GitHub 触发说明](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow)。

GitHub 公共仓库长期没有活动时可能停用 schedule，定时触发也可能延迟。它不是实时上游通知。可通过 Actions 页检查、重新启用和手动运行。

## 构建产物

普通 main 推送运行 Python 3.11 / 3.13 测试、核心校验、前端语法检查，再构建 Linux AMD64 / ARM64 镜像。

- `latest`：当前主分支镜像。
- `sha-<提交>`：用于固定版本与回退。
- `core-v<上游版本>`：该核心版本最近构建的 GUI 镜像；该标签会随 GUI 修改移动，回退使用 SHA 标签。
- `v<GUI版本>` 标签：发布对应版本镜像和源码安装包。

`scripts/package.py` 生成 `.tar.gz` 与 SHA256SUMS，包含 GUI、未修改的核心、安装脚本、文档与许可证，不打包本地状态、密码、Git 元数据或 Python 缓存。

创建 Git 标签触发发布包工作流。首次仓库上传不自动创建版本标签。GHCR 镜像的可见性和下载权限需要在 GitHub Packages 中检查。

## 设备更新

首版面板只检查上游版本，不下载源码覆盖正在运行的程序。

原生安装通过新版源码 / 安装包和安装脚本升级，Docker 通过拉取新镜像升级。两者都把数据放在独立的状态目录，升级时保持服务配置。

后续在线升级需要完整的下载校验、停机切换、备份、迁移与失败回退；不能只在面板内执行 git pull 或 curl | sh。

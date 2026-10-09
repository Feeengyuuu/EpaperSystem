# 用 Codex Cloud 远程修改 EpaperSystem

Codex Cloud 从 GitHub 仓库拉取代码，在云端环境中修改和测试，再通过分支或 Pull Request 交回变更。此文档提供项目的配置步骤；仓库中存在此文件不表示账户里的云端环境已经创建、发布或验证。

## 创建环境

1. 在 Codex 选择 **Work in → Cloud → Select environment → Create environment**，或进入 **Settings → Codex Cloud → Environments**。
2. 连接有权访问仓库的 GitHub 账户，选择 `Feeengyuuu/EpaperSystem`，基准分支选 `main`。如果看不到仓库，在 GitHub 连接设置中授予这个仓库的访问权限。
3. 点击 **Get started**，让环境设置代理按下方说明安装依赖并验证。项目测试使用 Python **3.11** 和 Node **24.12.0**；设置期间需要访问 Python 包源。
4. 检查安装和测试结果，保存设置并点击 **Publish**。出现 **Environment published** 后，再创建任务并选择这个环境。

界面流程参考 [Codex Cloud](https://learn.chatgpt.com/docs/cloud) 和 [Cloud environments](https://learn.chatgpt.com/docs/environments/cloud-environments)。GitHub 同步、云端环境发布、首个任务运行是三个独立的验收步骤。

## 给环境设置代理的说明

可以直接粘贴：

> 请为这个仓库建立开发环境，遵守根目录 AGENTS.md。使用 Python 3.11、Node 24.12.0；Python 应用在 inkypi-weather/package/InkyPi。先把 TMPDIR、TMP、TEMP 设置到仓库内的 .tmp/codex-cloud，再复用现有 scripts/venv.sh 安装带哈希锁定的开发依赖。不要执行树莓派安装脚本，也不要连接或部署到设备。运行 pip check、下方的环境验证测试，报告 Python、Node 版本和测试结果。保存设置后发布环境。

对应的 Bash 命令：

```bash
set -euo pipefail
REPO_ROOT="$(git rev-parse --show-toplevel)"
mkdir -p "$REPO_ROOT/.tmp/codex-cloud"
export TMPDIR="$REPO_ROOT/.tmp/codex-cloud"
export TMP="$TMPDIR" TEMP="$TMPDIR"
export PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1

PYTHON_BIN=python3.11
VENV_DIR="$REPO_ROOT/inkypi-weather/package/InkyPi/.venv"
source "$REPO_ROOT/inkypi-weather/package/InkyPi/scripts/venv.sh"
python --version
node --version
python -m pip check

cd "$REPO_ROOT/inkypi-weather/package/InkyPi"
python -m pytest tests/test_requirements_contract.py tests/test_clean_archive_tool.py \
  -q --no-header -p no:cacheprovider
```

环境验证通过后，可运行完整测试：

```bash
python -m pytest tests -q --no-header -p no:cacheprovider -o faulthandler_timeout=180
```

这些测试使用模拟硬件，完整套件中的 JavaScript 回归需要 Node。根目录 AGENTS.md 提供每个新任务的临时目录和 Python 路径设置；不要假设设置阶段的虚拟环境激活状态会自动保留到下一次 shell。

## 日常远程任务

发布环境后，从该环境创建任务，例如：

> 请在独立分支中修复【具体问题】，先阅读 AGENTS.md，保留已有工作。运行相关回归和必要的 CI 检查，完成后创建 Pull Request，说明改动及验证结果。本次不部署到墨水屏设备。

从任务差异或 Pull Request 审查结果，再合并到 `main`。需要实机更新时单独安排部署，并验证实际版本、终态任务及物理屏幕写入；云端测试通过不会自动更新正在运行的设备。

只有修改 Worker 时，才进入对应 `cloudflare` 子目录运行 `npm ci`、`npm run typecheck`、`npm test` 和 `npx wrangler deploy --dry-run --outdir dist`。`brief-reader` 在 typecheck 前还需 `npm run cf:types`，并运行 `python -m unittest discover -s tools -p "test_*.py"`。此处的 dry-run 只构建，不发布 Worker。

本机尚未提交的改动、其他工作树、SSH 密钥、`.env`、账户会话、设备配置和缓存不会随正常 Git 克隆同步。先保留和审查未完成代码，再按任务范围提交；无需把整个 Windows 项目目录打包上传，也无需给基础代码测试提供生产密钥。

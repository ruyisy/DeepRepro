# PaperBench Code-Dev 本地测试指导

## 当前标准配置

本地 PaperBench Code-Dev 评分固定使用下面这套配置，除非明确要做模型对照实验：

```text
base_url = https://aihubmix.com/v1
api_key  = <PAPERBENCH_JUDGE_API_KEY>
model    = gpt-4.1-mini
```

注意：这里的 key 是本地评测用 key，和 `mcp_agent.secrets.yaml` 里的项目生成侧模型配置无关。不要从 `mcp_agent.secrets.yaml` 读取 Judge 配置，避免误用 `qweapi`、DashScope 或其他供应商。

## 为什么用这套配置

- `gpt-4.1-mini` 的结构化输出和指令遵循更适合 PaperBench SimpleJudge。
- `SimpleJudge` 需要同时配置主 judge、int parser、float parser；否则 parser 可能回退到默认模型，导致额外失败。
- DeepSeek 官方 API 当前不适合作为完整 PaperBench SimpleJudge：`chat.completions.parse(response_format=...)` 会报 `This response_format type is unavailable now`。
- DashScope/deepseek-v4-flash 可以生成项目，但不作为当前稳定 Judge 标准。

## 运行前检查

在 PowerShell 中确认 WSL 和 PaperBench 环境存在：

```powershell
wsl -l -v
wsl -d Ubuntu-24.04 -- bash -lc "cd <PAPERBENCH_ROOT_WSL> && ./.venv/bin/python --version"
```

如果 WSL 不通，先修 WSL；不要直接改项目代码。

## DeepRepro FRE 评分

DeepRepro FRE 产物路径：

```text
<DEEPREPRO_ROOT>\deeprepro_code\task4\generate_code\fre_reproduction
```

对应 WSL 路径：

```text
<DEEPREPRO_ROOT_WSL>/deeprepro_code/task4/generate_code/fre_reproduction
```

评分脚本路径：

```text
<DEEPREPRO_ROOT>\deeprepro_code\task4\run_simple_judge_aihubmix_gpt41mini_full.py
```

运行命令：

```powershell
$env:PAPERBENCH_JUDGE_API_KEY="<PAPERBENCH_JUDGE_API_KEY>"
wsl -d Ubuntu-24.04 -- bash -lc "cd <PAPERBENCH_ROOT_WSL> && PYTHONUNBUFFERED=1 PAPERBENCH_JUDGE_API_KEY='$env:PAPERBENCH_JUDGE_API_KEY' ./.venv/bin/python <DEEPREPRO_ROOT_WSL>/deeprepro_code/task4/run_simple_judge_aihubmix_gpt41mini_full.py 2>&1 | tee <DEEPREPRO_ROOT_WSL>/deeprepro_code/task4/judge_simple_aihubmix_gpt41mini_full_run.log"
```

输出文件：

```text
<DEEPREPRO_ROOT>\deeprepro_code\task4\judge_simple_aihubmix_gpt41mini_full\grader_output.json
```

## DeepCode FRE 对照评分

DeepCode 原项目产物路径：

```text
<DEEPCODE_ROOT>\deepcode_lab\papers\1\generate_code\fre_reproduction
```

对应 WSL 路径：

```text
<DEEPCODE_ROOT_WSL>/deepcode_lab/papers/1/generate_code/fre_reproduction
```

评分脚本路径：

```text
<DEEPCODE_ROOT>\deepcode_lab\papers\1\run_simple_judge_aihubmix_gpt41mini_full.py
```

运行命令：

```powershell
$env:PAPERBENCH_JUDGE_API_KEY="<PAPERBENCH_JUDGE_API_KEY>"
wsl -d Ubuntu-24.04 -- bash -lc "cd <PAPERBENCH_ROOT_WSL> && PYTHONUNBUFFERED=1 PAPERBENCH_JUDGE_API_KEY='$env:PAPERBENCH_JUDGE_API_KEY' ./.venv/bin/python <DEEPCODE_ROOT_WSL>/deepcode_lab/papers/1/run_simple_judge_aihubmix_gpt41mini_full.py 2>&1 | tee <DEEPCODE_ROOT_WSL>/deepcode_lab/papers/1/judge_simple_aihubmix_gpt41mini_full_run.log"
```

输出文件：

```text
<DEEPCODE_ROOT>\deepcode_lab\papers\1\judge_simple_aihubmix_gpt41mini_full\grader_output.json
```

## 读取分数

```powershell
$jsonPath = "<DEEPREPRO_ROOT>\deeprepro_code\task4\judge_simple_aihubmix_gpt41mini_full\grader_output.json"
$result = Get-Content $jsonPath -Raw | ConvertFrom-Json
"score: {0} ({1})" -f $result.score, ($result.score * 100)
"leaf: {0}" -f $result.num_leaf_nodes
"invalid: {0}" -f $result.num_invalid_leaf_nodes
$result.token_usage | ConvertTo-Json -Depth 10
```

对 DeepCode 对照实验时，把 `$jsonPath` 改成 DeepCode 的 `grader_output.json` 路径。

## invalid 处理规则

PaperBench 输出里重点看三个数：

```text
score
num_leaf_nodes
num_invalid_leaf_nodes
```

解释规则：

- `raw score`：`grader_output.json` 里的原始 `score`，这是官方直接输出。
- `fact-corrected score`：如果 invalid leaf 的 `.log` 里有完整 `# Score`，可以按日志事实回填后重新传播树分数。
- `valid-count-adjusted score`：如果 invalid 是额度、网络等外部原因导致，并且没有最终 judge 内容，可以用 `raw_score * num_leaf_nodes / (num_leaf_nodes - num_invalid_leaf_nodes)` 做比例估计。
- `upper bound`：把所有 invalid 都按 `1` 计算，只能作为理论上界，不能当正式分数。

重要限制：比例估计只适合内部分析或论文中明确标注的 adjusted score。正式主结果最好使用完整无中断评测；如果 invalid 超过 `5%~10%`，优先补评失败 leaf 或重跑该论文。

## 常见失败原因

### 1. 余额不足

日志特征：

```text
403 Forbidden
insufficient_user_quota
Your account balance is insufficient
```

含义：judge 模型没有返回最终回答，所以 leaf `.log` 里可能只有 file selection，没有 `# Score`。这种 invalid 是外部评测失败，不代表项目产物被判差。

### 2. parser 失败

日志特征：模型已经输出了 `# Score`，但 `grader_output.json` 标记 `valid_score=false`。

处理：从对应 leaf `.log` 读取最终 `# Score`，按事实回填。

### 3. provider 不支持结构化解析

日志特征：

```text
This response_format type is unavailable now
```

处理：不要用该 provider 跑完整 SimpleJudge，除非修改 PaperBench parser 逻辑；当前标准使用 `aihubmix + gpt-4.1-mini`。

## 当前 FRE 已知结果

同一套 `aihubmix + gpt-4.1-mini` 配置下：

```text
DeepCode raw score              = 71.6092
DeepCode fact-corrected score   = 72.3752
DeepRepro raw score             = 69.0805
DeepRepro fact-corrected partial= 69.1436
DeepRepro valid-count-adjusted  = 78.2912
DeepRepro invalid reason        = 主要为 aihubmix 余额不足导致 judge 未返回
```

因此当前 FRE 单篇不能只看 DeepRepro raw score；那轮评测中途余额不足，导致 `36/306` 个 leaf invalid。用于内部判断时，`valid-count-adjusted` 更接近真实表现；用于正式论文时，建议重跑完整评测或明确标注 adjusted score。

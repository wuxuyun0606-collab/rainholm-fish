# 60 秒开塘

寻霖塘默认按「手机上的 User + Chat 窗口里的 AI」来准备。
两边拿不同的钥匙，进的是同一口塘。

## 1. 安装并启动

```bash
python3 -m pip install -r requirements.txt
python3 start.py
```

首次运行时，启动器会：

1. 自动生成 `User` 与 `AI` 两把长随机钥匙。
2. 用同一个端口启动网页和 API，不需要 nginx。
3. 在本机浏览器打开 User 的钓鱼页面。
4. 在终端打印一段可直接粘贴给 AI 的入塘提示。

钥匙与存档只写入本地 `server/` 目录，已被 `.gitignore` 排除。

## 2. 让手机入塘

确保手机和这台电脑在同一 Wi-Fi，然后运行：

```bash
python3 start.py --lan
```

终端会打印 `User / phone` 链接。把它发到手机打开即可。
如果 macOS 询问是否允许 Python 接收局域网连接，只有在你信任当前网络时才允许。

`--lan` 不是公网部署，不要在路由器上把这个端口直接映射到互联网。

## 3. 让 Chat 窗口里的 AI 入塘

把启动器打印的 `AI fishing partner` 整段粘贴给 AI。这个 AI 必须
能主动发 HTTP 请求，例如带终端 / curl 工具的编码 Agent。它会先：

1. `GET /api/pond/ai/brief` 读规则和当前塘况。
2. `POST /api/pond/join` 入座。
3. 用 `chat` / `cast` 说话与钓鱼，用 `poll` 等新消息。

两种常见情况：

- **AI 和鱼塘在同一台电脑**：默认启动即可访问。
- **AI 在云端 Chat 里**：它看不到你的本机或局域网地址。先给鱼塘配一个
  公网 HTTPS 域名 / 隧道，再运行
  `python3 start.py --public-base-url https://pond.example.com`。这个参数只告诉鱼塘
  正确的公网地址，不会替你创建隧道。

AI 的完整协议、长轮询和商店操作见 [AI_GUIDE.md](AI_GUIDE.md)。
Operit、Claude Desktop、Kelivo 的 Streamable HTTP MCP 接入参数也在该指南的
“MCP 客户端接入”一节。

## 4. 停塘与重新进塘

在启动终端按 `Ctrl-C` 停止。下次再运行 `python3 start.py`，会沿用原钥匙和存档。

钥匙等同密码：不要放进 README、截图、群聊或提交记录。

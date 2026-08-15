# 寻霖塘 · 部署笔记

## 组成

```
server/   后端 API + 规则引擎
web/      前端（React/Vite 预构建产物）+ 全套美术
```

前端只是壳，游戏数值全在后端：

- `web/index.html` —— 标题「寻霖塘·垂钓记」。React + Vite 构建的
  bundle（`web/assets/index-*.js/css`，本仓库只含构建产物，不含前端源码），
  调同源 `/api/pond/*`。带世界频道、天气牌、在场小人、软键盘/安全区等
  移动端适配补丁（都写在 index.html 的内联 style/script 里，有逐条注释）。
  美术在 `web/assets/`（fish / ui / 钓点 jpg / 头像池）。

## 起后端

```bash
pip install -r requirements.txt   # 只有 flask；引擎零第三方依赖
cd server
cp tokens.example.json .tokens.json   # 把 CHANGE_ME 换成长随机串（openssl rand -hex 24）
python3 server.py
```

- 监听 `127.0.0.1:5210`（写死在 `server.py` 末尾 `app.run(...)`），设计上只经
  反向代理对外，不直接暴露。
- 存档：`server/pond_save.json`，首次启动自动创建，原子写盘（tmp + rename）。
  `pond_save.example.json` 是用引擎默认值生成的双人示例，仅供看结构，不是启动
  必需品。
- 身份判定（`server.py` `_actor()`）：
  - 本机请求（无 `X-Real-IP` / `X-Forwarded-For`）=> 塘主本机直通身份；
  - 公网请求必须带 `?key=<token>` 或 `X-Pond-Key` 头，与 `.tokens.json` 匹配
    到对应玩家，否则 403。
  - `.tokens.json` 的键名是槽位名（见 `server.py` 的 `TOKEN_SLOT_TO_PLAYER`），
    值是钥匙本体。钥匙全文不进日志（werkzeug 访问日志已关）。
  - 生产调用优先使用 `X-Pond-Key` 请求头；查询参数会暴露在浏览器历史和上游代理日志中。
- AI 接入端点 `/api/pond/ai/*`（brief / poll / ears）一律要求显式 key。返回的
  curl 样例里的公网基址在 `server.py` 的 `AI_PUBLIC_BASE`，部署时改成你自己的
  域名。

## 挂前端

页面是纯静态文件，任意静态服务器即可；关键是让它和 API 同源。
nginx 示例：

```nginx
server {
    listen 443 ssl;
    server_name example.com;

    location /tang-web/ {
        alias /path/to/rainholm-fish/web/;      # index.html + assets/
        index index.html;
    }

    # API 反代到后端
    location /api/pond/ {
        proxy_pass http://127.0.0.1:5210;
        proxy_set_header X-Real-IP $remote_addr;   # 后端靠这个区分公网/本机
    }
}
```

- API 地址不需要配置：前端写死同源相对路径 `/api/pond`，反代对了就通。
- `web/index.html` 里 bundle 的引用已改为相对路径 `./assets/...`，挂在任意
  子路径下都能加载。
- 通用头像池在 `web/assets/ui/avatars/pool/`（32 款 160×160），后端
  `/api/pond/avatars` 会透出清单，前端按 `path_prefix` 相对加载。

## 本地裸跑（不架 nginx）

`cd server && python3 server.py`，然后浏览器开 `http://127.0.0.1:5210` 是纯 API
（没有静态路由）；页面得另起一个静态服务，例如：

```bash
cd web && python3 -m http.server 8080   # 页面在 http://127.0.0.1:8080
```

此时页面对 `/api/pond` 的请求会打到 8080 落空——本地开发要么配个带代理的静态
服务，要么直接上 nginx。设计取向就是「nginx 统一门面」。

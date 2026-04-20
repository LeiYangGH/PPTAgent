# DeepPresenter 问题排查与解决日志

> 本文档采用**日记追加形式**记录问题解决过程。每次新问题按以下格式追加到顶部：
> ```
> ## YYYY-MM-DD 问题标题
> 
> ### 现象
> 
> ### 根因分析
> 
> ### 解决步骤
> 
> ### 关键命令/代码
> 
> ### 经验总结
> ```

---

## 2026-04-21 Docker 镜像构建：国内网络环境下升级 Docker CLI

### 现象
需要构建包含新版 Docker CLI (29.4.1) 的镜像以解决 API 版本不兼容问题，但在国内网络环境下构建失败：
1. 拉取基础镜像 `node:lts-bookworm-slim` 超时
2. 安装 Docker CLI 时 GPG 密钥下载失败
3. 各种网络超时导致构建中断

### 根因分析
1. Docker Hub 官方源在国内访问不稳定
2. 构建过程中需要下载多个外部资源（apt 包、GPG 密钥、npm 包等）
3. 没有统一配置代理和镜像源

### 解决步骤
1. **修改 Dockerfile 使用镜像源**：
   - 基础镜像：`docker.1ms.run/node:lts-bookworm-slim`
   - apt 源：`mirrors.aliyun.com`
   - Docker GPG 和仓库：`mirrors.aliyun.com/docker-ce`

2. **配置构建时代理**：
   ```dockerfile
   ARG http_proxy=http://host.docker.internal:7890
   ARG https_proxy=http://host.docker.internal:7890
   ```

3. **分阶段构建确保每层成功**：
   - 先配置 apt 镜像源
   - 安装基础依赖（curl, gnupg）
   - 添加 Docker 仓库并安装 docker-ce-cli
   - 验证安装：`docker --version`

4. **构建命令**：
   ```bash
   docker compose build --no-cache
   ```

### 关键代码
```dockerfile
# 使用镜像源加速基础镜像拉取
FROM docker.1ms.run/node:lts-bookworm-slim

# 设置代理环境变量（构建时使用本地 7890 代理）
ARG http_proxy=http://host.docker.internal:7890
ARG https_proxy=http://host.docker.internal:7890
ARG no_proxy=localhost,127.0.0.1

# 配置 apt 使用阿里云镜像源
RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources || \
    sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list

# Install Docker CLI (使用代理和阿里云镜像)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates curl gnupg && \
    curl -fsSL -x http://host.docker.internal:7890 \
        https://mirrors.aliyun.com/docker-ce/linux/debian/gpg | apt-key add - && \
    echo "deb [arch=amd64] https://mirrors.aliyun.com/docker-ce/linux/debian bookworm stable" \
        > /etc/apt/sources.list.d/docker.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends docker-ce-cli && \
    apt-get clean && rm -rf /var/lib/apt/lists/* && \
    docker --version  # 验证安装
```

### 经验总结
- **镜像源选择**：`docker.1ms.run` 是国内可用的 Docker Hub 镜像
- **代理配置**：`host.docker.internal:7890` 让容器访问宿主机的代理
- **分层构建**：每步都加验证，便于排查问题
- **持久化解决**：镜像构建成功后，重启容器不会丢失 Docker CLI 版本
- **验证方式**：`docker run --rm deeppresenter-host:latest docker --version`

---

## 2026-04-21 Windows Docker Desktop 容器启动失败：docker 挂载错误

### 现象
重启电脑后执行 `docker compose up` 报错：
```
Error response from daemon: failed to create task for container: 
failed to create shim task: OCI runtime create failed: ...
error mounting "/usr/bin/docker" to rootfs at "/usr/bin/docker": 
mount src=/usr/bin/docker, dst=/usr/bin/docker, dstFd=/proc/thread-self/fd/11, 
flags=MS_BIND|MS_REC: not a directory
```

### 根因分析
1. `docker-compose.yml` 中挂载了 `/usr/bin/docker:/usr/bin/docker:ro`，这是 Linux 宿主机的路径
2. Windows 没有 `/usr/bin/docker`，且 Windows 的 docker.exe 无法在 Linux 容器中运行
3. 实际上 Host Dockerfile 已安装 `docker.io`，不需要从宿主机挂载

### 解决步骤
1. 修改 `docker-compose.yml`，移除 `/usr/bin/docker` 挂载
2. 重新启动容器：`docker compose up -d`

### 关键代码
```yaml
# docker-compose.yml 修改前
volumes:
  - /usr/bin/docker:/usr/bin/docker:ro  # 删除这行

# 修改后 - 完全移除该挂载
# Host Dockerfile 已安装 docker.io，容器自带 docker CLI
```

### 经验总结
- Windows Docker Desktop 不需要挂载宿主机的 docker 二进制文件
- 容器应自带 docker CLI，通过 `/var/run/docker.sock` 与 Docker daemon 通信
- 跨平台部署时需检查 volume 挂载的兼容性

---

## 2026-04-21 Docker API 版本不兼容：sandbox 无法启动

### 现象
WebUI 能正常访问，但提交 PPT 制作请求后本地 LLM 无日志，查看 `host.log` 发现：
```
docker: Error response from daemon: client version 1.41 is too old. 
Minimum supported API version is 1.44, please upgrade your client
ERROR ... Error connecting to server sandbox: Connection closed
```

### 根因分析
1. Host Dockerfile 安装的是 Debian 仓库的 `docker.io` (v20.10.24)，API 版本 1.41
2. Docker Desktop for Windows 使用较新的 API 版本 (1.44+)
3. 容器内的 docker CLI 与 Docker daemon API 版本不兼容，无法创建 sandbox 容器

### 解决步骤
1. 进入运行中的容器：`docker exec -it deeppresenter-host bash`
2. 添加阿里云 Docker 镜像源 GPG 密钥：
   ```bash
   curl -fsSL https://mirrors.aliyun.com/docker-ce/linux/debian/gpg | apt-key add -
   ```
3. 添加阿里云镜像源：
   ```bash
   echo 'deb [arch=amd64] https://mirrors.aliyun.com/docker-ce/linux/debian bookworm stable' > /etc/apt/sources.list.d/docker.list
   ```
4. 更新并安装新版 docker-ce-cli：
   ```bash
   apt-get update
   apt-get install -y docker-ce-cli
   ```
5. 验证版本：`docker --version` → `Docker version 29.4.1`
6. 重启容器：`docker restart deeppresenter-host`

### 关键命令
```bash
# 在容器内执行
curl -fsSL https://mirrors.aliyun.com/docker-ce/linux/debian/gpg | apt-key add -
echo 'deb [arch=amd64] https://mirrors.aliyun.com/docker-ce/linux/debian bookworm stable' > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce-cli
docker --version  # 验证：29.4.1
docker ps         # 验证：能列出容器
```

### 经验总结
- Docker CLI 与 Daemon 的 API 版本必须兼容
- Debian 默认仓库的 docker.io 版本较旧，建议使用官方/阿里云镜像源安装 docker-ce-cli
- Windows Docker Desktop 对 API 版本要求较高，需保持客户端更新
- 修复后需重启容器让 WebUI 重新初始化 MCP 连接

---

# DeepPresenter

本项目是 [icip-cas/PPTAgent](https://github.com/icip-cas/PPTAgent) 的fork版本，主要针对中国国内网络环境和本地大模型进行了适配和优化。

## Docker 部署

### 前置要求

- Docker 版本：建议 29.2.0 或更高版本
- Docker Desktop 需要启用 host 网络访问：
  - 打开 Docker Desktop
  - 进入 Settings → Resources
  - 勾选 "Enable host networking"

### 使用预构建镜像

预构建的 Docker 镜像已打包，可直接下载使用。

```
通过网盘分享的文件：
链接: https://pan.baidu.com/s/11LiFiJZ3aXx9Y7svv_7z7A?pwd=sq6y 提取码: sq6y 复制这段内容后打开百度网盘手机App，操作更方便哦
```

#### 加载镜像

```bash
docker load -i <文件名>.tar
```

#### 配置文件

示例配置文件位于：
```
deeppresenter/Qwen3.6-35B-A3B.config/config.yaml
```

**注意**：配置中使用 `host.docker.internal` 访问宿主机服务，需要先在 Docker Desktop 中启用 host networking。

#### 启动服务

```bash
docker compose up -d
```

#### 查看日志

```bash
docker logs deeppresenter-host -f
```

### 镜像信息

- `deeppresenter-host:latest` - 主服务镜像
- `deeppresenter-sandbox:latest` - 沙箱环境镜像
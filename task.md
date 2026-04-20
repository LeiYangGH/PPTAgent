帮我检查配置和步骤，解决本地运行的问题

我当前的环境是win11，我希望运行当前项目。
我用的docker desktop，设置了host共享网络
目前已经按说明pull了image。

```
IMAGE                                                   ID             DISK USAGE   CONTENT SIZE   EXTRA
deeppresenter-host:latest                               d1a303c21638       7.79GB         2.32GB    U
deeppresenter-sandbox:latest                            eba8ec357f8c       6.09GB          1.8GB
docker.1ms.run/forceless/deeppresenter-host:latest      d1a303c21638       7.79GB         2.32GB    U
docker.1ms.run/forceless/deeppresenter-sandbox:latest   eba8ec357f8c       6.09GB          1.8GB
forceless/deeppresenter-sandbox:latest                  eba8ec357f8c       6.09GB          1.8GB
ghcr.io/presenton/presenton:latest                      c80ed6bb29b9       18.5GB          5.6GB
```

修改了些配置(你可以自由探查git 状态和历史)
见
- docker-compose.yml
- .env

我先运行了本地llm
```
llama-server -m  "D:\models\Qwen3.6-35B-A3B-GGUF\Qwen3.6-35B-A3B-UD-Q3_K_XL.gguf" --port 8989
```

也运行了docker compose up -d --force-recreate

但是浏览器打开http://localhost:7861/，输入提示词 `针对儿童创作自然灾害科普的ppt`

但没有任何结果 而且我后台llm日志也显示没收到任何请求

服务器日志见 host.log

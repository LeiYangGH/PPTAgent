# 使用镜像源加速基础镜像拉取
FROM docker.1ms.run/node:lts-bookworm-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# 设置代理环境变量（构建时使用本地 7890 代理）
ARG http_proxy=http://host.docker.internal:7890
ARG https_proxy=http://host.docker.internal:7890
ARG no_proxy=localhost,127.0.0.1

# 配置 apt 使用镜像源
RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources || \
    sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list

# Install ca-certificates first to avoid GPG signature issues, then other packages
RUN apt-get update && \
    apt-get install -y --fix-missing --no-install-recommends ca-certificates && \
    update-ca-certificates && \
    apt-get install -y --no-install-recommends git bash curl wget unzip ripgrep vim sudo g++ locales

SHELL ["/bin/bash", "-o", "pipefail", "-c"]
RUN sed -i '/en_US.UTF-8/s/^# //g' /etc/locale.gen && locale-gen

# Install Chromium and dependencies

RUN apt-get update && apt-get install -y --fix-missing --no-install-recommends \
        chromium \
        fonts-liberation \
        libappindicator3-1 \
        libasound2 \
        libatk-bridge2.0-0 \
        libatk1.0-0 \
        libcups2 \
        libdbus-1-3 \
        libdrm2 \
        libgbm1 \
        libgtk-3-0 \
        libnspr4 \
        libnss3 \
        libx11-xcb1 \
        libxcomposite1 \
        libxdamage1 \
        libxrandr2 \
        xdg-utils \
        fonts-dejavu \
        fonts-noto \
        fonts-noto-cjk \
        fonts-noto-cjk-extra \
        fonts-noto-color-emoji \
        fonts-freefont-ttf \
        fonts-urw-base35 \
        fonts-roboto \
        fonts-wqy-zenhei \
        fonts-wqy-microhei \
        fonts-arphic-ukai \
        fonts-arphic-uming \
        fonts-ipafont \
        fonts-ipaexfont \
        fonts-comic-neue \
        imagemagick

WORKDIR /usr/src/pptagent

COPY . .

RUN npm install --prefix deeppresenter/html2pptx --ignore-scripts && \
    npm exec --prefix deeppresenter/html2pptx playwright install chromium && \
    npm install --prefix /root/.cache/deeppresenter/html2pptx fast-glob minimist pptxgenjs playwright sharp

# Set environment variables
ENV PATH="/opt/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV="/opt/.venv" \
    DEEPPRESENTER_WORKSPACE_BASE="/opt/workspace"

# Create Python virtual environment and install packages
RUN uv venv --python 3.13 $VIRTUAL_ENV && \
    uv pip install -e .

# Install Python Playwright browser binaries used by deeppresenter runtime.
RUN /opt/.venv/bin/playwright install chromium
RUN modelscope download --model forceless/fasttext-language-id

# Install poppler-utils for PDF processing
RUN apt install -y poppler-utils

# Install Docker CLI (from Aliyun mirror for China, using proxy)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates curl gnupg && \
    curl -fsSL -x http://host.docker.internal:7890 https://mirrors.aliyun.com/docker-ce/linux/debian/gpg | apt-key add - && \
    echo "deb [arch=amd64] https://mirrors.aliyun.com/docker-ce/linux/debian bookworm stable" > /etc/apt/sources.list.d/docker.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends docker-ce-cli && \
    apt-get clean && rm -rf /var/lib/apt/lists/* && \
    docker --version

RUN fc-cache -f

CMD ["bash", "-c", "umask 000 && python webui.py 0.0.0.0"]

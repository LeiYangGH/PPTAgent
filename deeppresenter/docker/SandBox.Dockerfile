FROM docker.1ms.run/node:lts-bookworm-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Configure apt mirror (Aliyun for China)
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

# Configure npm to use Taobao registry for China
RUN npm config set registry https://registry.npmmirror.com

# Use npmmirror for Playwright browser downloads (China)
ENV PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright

RUN npm install --prefix deeppresenter/html2pptx --ignore-scripts && \
    npm exec --prefix deeppresenter/html2pptx playwright install chromium && \
    npm install --prefix /root/.cache/deeppresenter/html2pptx fast-glob minimist pptxgenjs playwright sharp

WORKDIR /usr/src/app

# Configure npm to use Taobao registry for China
RUN npm config set registry https://registry.npmmirror.com

# Puppeteer config for mermaid-cli
RUN echo '{"args":["--no-sandbox","--disable-setuid-sandbox"]}' > /root/.puppeteerrc.json

# Set environment variables (for CMD and non-shell contexts)
ENV PATH="/opt/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV="/opt/.venv" \
    PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true \
    PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium \
    LANG=en_US.UTF-8 \
    LC_ALL=en_US.UTF-8 \
    MPLCONFIGDIR=/etc/matplotlib \
    MCP_CLIENT_DOCKER=true

# Export ENV to /etc/profile.d/ for bash -lc and interactive shells
RUN printenv | grep -E '^(PATH|PYTHONUNBUFFERED|VIRTUAL_ENV|PUPPETEER_|LANG|LC_ALL|MPLCONFIGDIR|MCP_CLIENT_DOCKER)=' | sed 's/^/export /' > /etc/profile.d/docker-env.sh && \
    echo 'source /etc/profile.d/docker-env.sh' >> /etc/bash.bashrc

# Clone the repository at specific commit (shallow clone to save time/bandwidth)
RUN git clone --depth 1 https://gitee.com/leimiemie/DesktopCommanderMCP.git . && \
    git fetch --depth 1 origin 252a00d624c2adc5707fa743c57a1b68bc223689 && \
    git checkout 252a00d624c2adc5707fa743c57a1b68bc223689 && \
    rm -rf .git

RUN npm install --ignore-scripts && npm install -g @mermaid-js/mermaid-cli pptxgenjs playwright sharp

# Create Python virtual environment and install packages
# Configure uv to use Tsinghua PyPI mirror for China
RUN uv venv --python 3.13 $VIRTUAL_ENV && \
    uv pip install -i https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple pip python-pptx matplotlib seaborn plotly numpy pandas opencv-python-headless pillow

# Install LibreOffice for PPT preview conversion (unoconvert/soffice)
RUN apt-get update && \
    apt-get install -y --no-install-recommends libreoffice-impress libreoffice-writer && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Copying config and tailored server files
COPY deeppresenter/docker/config.json /root/.claude-server-commander/config.json
COPY deeppresenter/docker/server.ts src/server.ts
COPY deeppresenter/docker/improved-process-tools.ts src/tools/improved-process-tools.ts

# Configure matplotlib for CJK fonts and emoji fallback
RUN fc-cache -f && \
    mkdir -p /etc/matplotlib && \
    printf '%s\n' \
      'font.family: sans-serif' \
      'font.sans-serif: Noto Sans CJK SC, Noto Color Emoji, WenQuanYi Zen Hei, DejaVu Sans' \
      'axes.unicode_minus: False' \
      > /etc/matplotlib/matplotlibrc

# Rebuild the package
RUN npm run build

CMD ["node",  "/usr/src/app/dist/index.js", "--no-onboarding"]

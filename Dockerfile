FROM python:3.13-slim

WORKDIR /app

# 安装系统依赖，并同时安装 Node.js、EJS 和 Deno
RUN apt-get update -y \
    && apt-get install -y --no-install-recommends ffmpeg curl unzip git \
    && curl -fsSL https://deb.nodesource.com/setup_18.x | bash - \
    && apt-get install -y nodejs \
    && npm install -g ejs \
    && curl -fsSL https://deno.land/install.sh | sh \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# 设置 Deno 环境变量
ENV DENO_INSTALL="/root/.deno"
ENV PATH="${DENO_INSTALL}/bin:${PATH}"

# 安装 Python 依赖
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目文件
COPY . .

# 直接运行 Python 模块
CMD ["python", "-m", "HasiiMusic"]
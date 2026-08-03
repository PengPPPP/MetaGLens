# MetaGLens showcase — ModelScope Studio (Docker 类型)部署镜像
#
# 魔搭创空间 Docker 类型要求服务监听 0.0.0.0:7860(8080 被平台占用)。
# 演示站只暴露只读接口 + 有界演示队列(桩工具、临时目录、跑完即清),
# 不写任何用户文件、不执行任意命令。
FROM python:3.10-slim

WORKDIR /app

# 1) 构建依赖 + 包本体(含 templates 等 package-data)
COPY pyproject.toml README.md /app/
COPY metaglens /app/metaglens
RUN pip install --no-cache-dir PyYAML typer rich \
    && pip install --no-cache-dir /app

# 2) 运行端口(魔搭规定)
EXPOSE 7860

# 3) 起演示站。桩工具 + 合成数据,不产生科学结果。
CMD ["metaglens", "showcase", "--host", "0.0.0.0", "--port", "7860"]

FROM mirror.gcr.io/python:3.10
WORKDIR /work
RUN apt-get update && apt-get install -y --no-install-recommends libqhull-dev libfreetype6-dev pkg-config && rm -rf /var/lib/apt/lists/*
COPY . .
RUN bash /work/setup_matplotlib__matplotlib.sh
ENTRYPOINT ["bash"]
CMD ["/work/test_matplotlib__matplotlib.sh"]

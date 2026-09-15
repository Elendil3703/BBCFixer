FROM mirror.gcr.io/python:3.12
WORKDIR /work
RUN apt-get update && apt-get install -y --no-install-recommends build-essential cargo rustc && rm -rf /var/lib/apt/lists/*
COPY . .
RUN bash /work/setup_amphibian-dev__toad.sh
ENTRYPOINT ["bash"]
CMD ["/work/test_amphibian-dev__toad.sh"]

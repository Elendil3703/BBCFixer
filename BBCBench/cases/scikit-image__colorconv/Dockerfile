FROM mirror.gcr.io/python:3.11
WORKDIR /work
RUN apt-get update && apt-get install -y --no-install-recommends build-essential pkg-config && rm -rf /var/lib/apt/lists/*
COPY . .
RUN bash /work/setup_scikit-image__colorconv.sh
ENTRYPOINT ["bash"]
CMD ["/work/test_scikit-image__colorconv.sh"]

FROM mirror.gcr.io/python:3.12
WORKDIR /work
COPY . .
RUN bash /work/setup_docker__docker-py.sh
ENTRYPOINT ["bash"]
CMD ["/work/test_docker__docker-py.sh"]

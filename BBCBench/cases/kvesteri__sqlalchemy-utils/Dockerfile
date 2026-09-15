FROM mirror.gcr.io/python:3.10
WORKDIR /work

COPY . .
RUN bash /work/setup_kvesteri__sqlalchemy-utils.sh
ENTRYPOINT ["bash"]
CMD ["/work/test_kvesteri__sqlalchemy-utils.sh"]

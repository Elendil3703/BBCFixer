FROM mirror.gcr.io/python:3.12
WORKDIR /work
COPY . .
RUN bash /work/setup_kevin1024__vcrpy.sh
ENTRYPOINT ["bash"]
CMD ["/work/test_kevin1024__vcrpy.sh"]

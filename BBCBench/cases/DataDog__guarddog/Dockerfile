FROM mirror.gcr.io/python:3.12
WORKDIR /work
COPY . .
RUN bash /work/setup_DataDog__guarddog.sh
ENTRYPOINT ["bash"]
CMD ["/work/test_DataDog__guarddog.sh"]

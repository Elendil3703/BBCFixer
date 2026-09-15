FROM mirror.gcr.io/python:3.10
WORKDIR /work
COPY . .
RUN bash /work/setup_getmoto__moto.sh
ENTRYPOINT ["bash"]
CMD ["/work/test_getmoto__moto.sh"]

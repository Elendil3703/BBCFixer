FROM mirror.gcr.io/python:3.10
WORKDIR /work

COPY . .
RUN bash /work/setup_Kinto__kinto.sh
ENTRYPOINT ["bash"]
CMD ["/work/test_Kinto__kinto.sh"]

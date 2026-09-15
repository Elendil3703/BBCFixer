FROM mirror.gcr.io/python:3.9
WORKDIR /work

COPY . .
RUN bash /work/setup_pygae__clifford.sh
ENTRYPOINT ["bash"]
CMD ["/work/test_pygae__clifford.sh"]

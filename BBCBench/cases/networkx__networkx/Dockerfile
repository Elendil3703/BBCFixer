FROM mirror.gcr.io/python:3.10
WORKDIR /work

COPY . .
RUN bash /work/setup_networkx__networkx.sh
ENTRYPOINT ["bash"]
CMD ["/work/test_networkx__networkx.sh"]

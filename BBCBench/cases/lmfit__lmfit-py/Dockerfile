FROM mirror.gcr.io/python:3.7
WORKDIR /work

COPY . .
RUN bash /work/setup_lmfit__lmfit-py.sh
ENTRYPOINT ["bash"]
CMD ["/work/test_lmfit__lmfit-py.sh"]

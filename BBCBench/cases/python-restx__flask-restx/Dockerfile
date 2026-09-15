FROM mirror.gcr.io/python:3.9
WORKDIR /work
COPY . .
RUN bash /work/setup_python-restx__flask-restx.sh
ENTRYPOINT ["bash"]
CMD ["/work/test_python-restx__flask-restx.sh"]

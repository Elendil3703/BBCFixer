FROM mirror.gcr.io/python:3.9
WORKDIR /work

COPY . .
RUN bash /work/setup_postmanlabs__httpbin.sh
ENTRYPOINT ["bash"]
CMD ["/work/test_postmanlabs__httpbin.sh"]

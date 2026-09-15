FROM mirror.gcr.io/python:3.9
WORKDIR /work

COPY . .
RUN bash /work/setup_FrescolinoGroup__pyhdf5io.sh
ENTRYPOINT ["bash"]
CMD ["/work/test_FrescolinoGroup__pyhdf5io.sh"]

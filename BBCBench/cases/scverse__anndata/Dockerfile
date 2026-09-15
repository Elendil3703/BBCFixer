FROM mirror.gcr.io/python:3.8
WORKDIR /work

COPY . .
RUN bash /work/setup_scverse__anndata.sh
ENTRYPOINT ["bash"]
CMD ["/work/test_scverse__anndata.sh"]

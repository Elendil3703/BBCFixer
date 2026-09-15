FROM mirror.gcr.io/python:3.11
WORKDIR /work

COPY . .
RUN bash /work/setup_pydata__xarray.sh
ENTRYPOINT ["bash"]
CMD ["/work/test_pydata__xarray.sh"]

FROM mirror.gcr.io/python:3.10
WORKDIR /work

COPY . .
RUN bash /work/setup_alteryx__featuretools.sh
ENTRYPOINT ["bash"]
CMD ["/work/test_alteryx__featuretools.sh"]

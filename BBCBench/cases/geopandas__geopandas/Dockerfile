FROM mirror.gcr.io/python:3.10
WORKDIR /work

COPY . .
RUN bash /work/setup_geopandas__geopandas.sh
ENTRYPOINT ["bash"]
CMD ["/work/test_geopandas__geopandas.sh"]

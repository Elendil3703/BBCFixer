FROM mirror.gcr.io/python:3.12
WORKDIR /work
RUN apt-get update && apt-get install -y --no-install-recommends gcc gfortran && rm -rf /var/lib/apt/lists/*
COPY . .
RUN bash /work/setup_obspy__obspy.sh
ENTRYPOINT ["bash"]
CMD ["/work/test_obspy__obspy.sh"]

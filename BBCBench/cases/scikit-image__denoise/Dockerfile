FROM mirror.gcr.io/python:3.11
WORKDIR /work

COPY . .
RUN bash /work/setup_scikit-image__denoise.sh
ENTRYPOINT ["bash"]
CMD ["/work/test_scikit-image__denoise.sh"]

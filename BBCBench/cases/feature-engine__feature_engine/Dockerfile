FROM mirror.gcr.io/python:3.10
WORKDIR /work

COPY . .
RUN bash /work/setup_feature-engine__feature_engine.sh
ENTRYPOINT ["bash"]
CMD ["/work/test_feature-engine__feature_engine.sh"]

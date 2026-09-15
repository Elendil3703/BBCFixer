FROM mirror.gcr.io/python:3.10
WORKDIR /work

COPY . .
RUN bash /work/setup_scikit-learn-contrib__imbalanced-learn.sh
ENTRYPOINT ["bash"]
CMD ["/work/test_scikit-learn-contrib__imbalanced-learn.sh"]

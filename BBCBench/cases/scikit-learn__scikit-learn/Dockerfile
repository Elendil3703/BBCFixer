FROM mirror.gcr.io/python:3.10
WORKDIR /work

COPY . .
RUN bash /work/setup_scikit-learn__scikit-learn.sh
ENTRYPOINT ["bash"]
CMD ["/work/test_scikit-learn__scikit-learn.sh"]

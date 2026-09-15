FROM mirror.gcr.io/python:3.9
WORKDIR /work

COPY . .
RUN bash /work/setup_telegraphic__hickle.sh
ENTRYPOINT ["bash"]
CMD ["/work/test_telegraphic__hickle.sh"]

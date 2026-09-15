FROM mirror.gcr.io/python:3.12.11

ENV PIP_INDEX_URL=http://host.docker.internal:5001/snapshot/2025-07-31
ENV PIP_TRUSTED_HOST=host.docker.internal

WORKDIR /work

COPY dakrauth__django-swingtime .

RUN bash /work/setup_dakrauth__django-swingtime.sh

ENTRYPOINT ["bash"]
CMD ["/work/test_dakrauth__django-swingtime.sh"]

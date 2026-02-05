# Python Lambda base image
FROM public.ecr.aws/lambda/python:3.12

# Install Python dependencies into the Lambda task root
COPY ./src/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -t ${LAMBDA_TASK_ROOT}

# Copy application code
COPY ./src/app.py ${LAMBDA_TASK_ROOT}/app.py

# Set the Lambda handler
CMD [ "app.handler" ]

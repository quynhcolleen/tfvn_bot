import boto3

ACCESS_KEY_ID="b7b6d1a4f0fe411fb88cf6bfde95dc5b"
SECRET_KEY_ACCESS="f0f66ef4481abc8400dc10f21e5fa901dabca7ca082379f0fab25873329cc753"
S3_CLIENT_ENDPOINT="https://b252b0d92370e4e9509e6b8eb1fdab0e.r2.cloudflarestorage.com/tfvn-bucket"


s3 = boto3.client(
    service_name="s3",
    # Provide your Cloudflare account ID
    endpoint_url=S3_CLIENT_ENDPOINT,
    # Retrieve your S3 API credentials for your R2 bucket via API tokens (see: https://developers.cloudflare.com/r2/api/tokens)
    aws_access_key_id=ACCESS_KEY_ID,
    aws_secret_access_key=SECRET_KEY_ACCESS,
    region_name="auto", # Required by SDK but not used by R2
)

get_url = s3.generate_presigned_url(
    'get_object',
    Params={'Bucket': 'tfvn-bucket', 'Key': '1.gif'},
    ExpiresIn=3600  # Valid for 1 hour
)

# check if file is existing
file_exists = s3.head_object(Bucket='tfvn-bucket', Key='1.gif')

print("File exists:", file_exists)

print("Generated presigned URL:", get_url)

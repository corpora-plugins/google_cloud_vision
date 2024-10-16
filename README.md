# google-cloud-vision

The Corpora plugin for performing OCR using Google Cloud Vision.

## Configuration for OCR Jobs

To run Google Cloud Vision OCR jobs, you'll need to [do some setup on the Google side of the fence](https://cloud.google.com/vision/docs/setup), create a service account, download the key for that account as a JSON file in a secure location accessible by Corpora, and set the `GOOGLE_APPLICATION_CREDENTIALS` environment variable inside the Corpora container.

Since Google Cloud Vision API calls cost money, you must also set the "Google Cloud Vision OCR Credits" key/value pair for any corpus you'd like to perform OCR for. At present, the only way to do that is with the [Python Corpus API](https://bptarpley.github.io/corpora/developing/#the-corpus-api-for-python):

```python
# Allow users to perform Google Cloud Vision OCR on 1,000 document pages
my_corpus.kvp["Google Cloud Vision OCR Credits"] = 1000
my_corpus.save()
```

Finally, Google Cloud Vision OCR jobs can only be launched from within instances of the Document content type as defined by the `Document` plugin that comes built-in to Corpora.
import io
import json
import requests
import traceback
from time import sleep
from django.utils.text import slugify
from huey.contrib.djhuey import db_task
from google.cloud import vision
from google.protobuf.json_format import MessageToJson
from corpus import *


REGISTRY = {
    "OCR Document with Google Cloud Vision": {
        "version": "0.1",
        "jobsite_type": "HUEY",
        "track_provenance": True,
        "create_report": True,
        "content_type": "Document",
        "configuration": {
            "parameters": {
                "name": {
                    "value": "",
                    "type": "text",
                    "label": "Unique Name for this OCR Job"
                },
                "collection": {
                    "value": "",
                    "type": "page_file_collection",
                    "label": "Page Image Collection",
                    "note": "Be sure to select a collection consisting of images."
                },
                "pageset": {
                    "value": "",
                    "type": "document_pageset",
                    "label": "Page Set",
                    "note": 'Choose "All Pages" to OCR every page, or select a page set to OCR a subset of pages.'
                },
            },
        },
        "module": 'plugins.google_cloud_vision.tasks',
        "functions": ['ocr_document_with_google_cloud_vision']
     }
}


@db_task(priority=2)
def ocr_document_with_google_cloud_vision(job_id):
    job = Job(job_id)
    job.set_status('running')

    try:
        page_file_collection_key = job.get_param_value('collection')
        pageset_key = job.get_param_value('pageset')
        page_files = job.content.page_file_collections[page_file_collection_key]['page_files']
        ref_nos = []

        iiif_images = False
        for ref_no, page_file in page_files:
            if page_file['iiif_info']:
                iiif_images = True
            break

        if pageset_key == "none":
            ref_nos = page_files.ordered_ref_nos
        elif pageset_key in job.content.page_sets:
            ref_nos = [ref_no for ref_no in page_files.ordered_ref_nos if
                       ref_no in job.content.page_sets[pageset_key].ref_nos]

        num_pages = len(ref_nos)

        if 'Google Cloud Vision OCR Credits' in job.corpus.kvp and \
                0 < num_pages <= job.corpus.kvp['Google Cloud Vision OCR Credits']:

            job.report(f"Attempting to OCR {num_pages} pages for page file collection {page_file_collection_key}.")
            if pageset_key != "none":
                job.report(f"Limiting pages to those found in page set {job.content.page_sets[pageset_key].label}.")

            for ref_no in ref_nos:
                huey_task = ocr_page_with_google_cloud_vision(job_id, ref_no)
                job.add_process(huey_task.id)
                job.corpus.kvp['Google Cloud Vision OCR Credits'] -= 1

                # we don't want to burden a IIIF server, so let's throttle image requests
                if iiif_images:
                    sleep(3)

            job.corpus.save()
        else:
            job.report("Either this corpus has no more Google Cloud Vision OCR credits, or no valid pages found to OCR.")

    except:
        error = traceback.format_exc()
        job.report(error)
        job.complete('error', error_msg=error)

# todo: make one page at a time
@db_task(priority=1, context=True)
def ocr_page_with_google_cloud_vision(job_id, assigned_ref_no, task=None):
    job = Job(job_id)
    file_size_limit = 9500000
    client = vision.ImageAnnotatorClient()

    ocr_job_name = job.get_param_value('name')
    page_file_collection_key = job.get_param_value('collection')
    page_files = job.content.page_file_collections[page_file_collection_key]['page_files']

    for ref_no, page_file in page_files:
        if ref_no == assigned_ref_no:
            page_file_dir = f"{job.content.path}/pages/{ref_no}"
            os.makedirs(page_file_dir, exist_ok=True)

            if page_file['iiif_info'] or os.path.exists(page_file['path']):
                # base path for different outputs
                page_file_results = f"{page_file_dir}/GCV-OCR_{slugify(ocr_job_name)}_{ref_no}"
                page_file_path = page_file['path']
                download_url = None

                if page_file['iiif_info']:
                    # set file path to None because we're not working with a local file
                    page_file_path = None

                    # check to see if the IIIF URI is valid
                    uri_check = requests.head(f"{page_file['path']}/info.json")
                    if uri_check.status_code == 200:
                        # limit width of file in attempt to ensure downloaded image isn't too big for GCV API
                        image_width = page_file['width']
                        if image_width > 1000:
                            image_width = 1000

                        region = "full"
                        if 'fixed_region' in page_file['iiif_info']:
                            fixed_r = page_file['iiif_info']['fixed_region']
                            region = "{x},{y},{w},{h}".format(
                                x=fixed_r['x'],
                                y=fixed_r['y'],
                                w=fixed_r['w'],
                                h=fixed_r['h']
                            )

                        download_url = "{id}/{region}/{width},/0/gray.png".format(
                            id=page_file['path'],
                            region=region,
                            width=image_width
                        )

                        job.report(f"Performing OCR on page {ref_no} ({download_url})")

                    else:
                        job.report(f"Page {ref_no} has an unresponsive IIIF identifier. Cannot perform OCR.")

                # now that we have either a file path or a URL, let's create image content to pass to the GCV endpoint
                gcv_image = None

                # our image is a local file, so we need to ensure it's small enough for GCV and read in its bytes
                if page_file_path:
                    job.report(f"Performing OCR on page {ref_no} ({page_file['path'].replace(job.content.path, '')})")

                    file_size = page_file['byte_size']
                    if file_size > file_size_limit:
                        job.report(f"Page {ref_no} is too large. Downsizing image...")
                        extension = '.' + page_file_path.split('.')[-1]
                        small_image_path = "{0}/pages/{1}/{2}".format(
                            job.document.path,
                            page_file['page'],
                            os.path.basename(page_file_path).replace(extension, "_downsized" + extension),
                        )
                        small_width = 3000
                        img = Image.open(page_file_path)
                        width_percent = (small_width / float(img.size[0]))
                        small_height = int((float(img.size[1]) * float(width_percent)))
                        img.thumbnail((small_width, small_height), Image.ANTIALIAS)
                        img.save(small_image_path)
                        if os.path.exists(small_image_path):
                            page_file_path = small_image_path

                    with io.open(page_file_path, 'rb') as page_contents:
                        image_content = page_contents.read()
                        gcv_image = vision.Image(content=image_content)

                elif download_url:
                    gcv_image = vision.Image()
                    gcv_image.source.image_uri = download_url

                if gcv_image:
                    api_response = client.document_text_detection(image=gcv_image)
                    if api_response.error.message:
                        job.report(f"Page {ref_no} triggered an API error: {api_response.error.message}")

                    ocr = api_response.full_text_annotation

                    with open(page_file_results + '.txt', 'w', encoding="utf-8") as text_out:
                        text_out.write(ocr.text)

                    breaks = vision.TextAnnotation.DetectedBreak.BreakType
                    html = "<html><head></head><body>"
                    for page in ocr.pages:
                        html += "<div>"
                        for block in page.blocks:
                            html += "<div>"
                            for paragraph in block.paragraphs:
                                html += "<p>"
                                for word in paragraph.words:
                                    for symbol in word.symbols:
                                        html += symbol.text
                                        if symbol.property.detected_break.type == breaks.SPACE:
                                            html += ' '
                                        elif symbol.property.detected_break.type == breaks.EOL_SURE_SPACE:
                                            html += '<br />'
                                        elif symbol.property.detected_break.type == breaks.LINE_BREAK:
                                            html += '<br />'
                                        elif symbol.property.detected_break.type == breaks.HYPHEN:
                                            html += '-<br />'
                                html += "</p>"
                            html += "</div>"
                        html += "</div>"
                    html += "</body></html>"

                    with open(page_file_results + '.html', 'w', encoding="utf-8") as html_out:
                        html_out.write(html)

                    with open(page_file_results + '.json', 'w', encoding="utf-8") as json_out:
                        json_out.write(MessageToJson(ocr._pb))

                    txt_file_obj = File.process(
                        page_file_results + '.txt',
                        desc='Plain Text',
                        prov_type=f'Google Cloud Vision OCR Job ({ocr_job_name})',
                        prov_id=str(job_id),
                    )
                    if txt_file_obj:
                        job.content.save_page_file(ref_no, txt_file_obj)

                    html_file_obj = File.process(
                        page_file_results + '.html',
                        desc='HTML',
                        prov_type=f'Google Cloud Vision OCR Job ({ocr_job_name})',
                        prov_id=str(job_id),
                    )
                    if txt_file_obj:
                        job.content.save_page_file(ref_no, html_file_obj)

                    gcv_json_obj = File.process(
                        page_file_results + '.json',
                        desc='JSON',
                        prov_type=f'Google Cloud Vision OCR Job ({ocr_job_name})',
                        prov_id=str(job_id),
                    )
                    if gcv_json_obj:
                        job.content.save_page_file(ref_no, gcv_json_obj)

    if task:
        job.complete_process(task.id)

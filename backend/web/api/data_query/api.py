from flask import request
from flask_restful import Resource

from services import (
    create_data_query_task,
    delete_data_query_task,
    generate_data_query_export,
    get_data_query_preview,
    get_data_query_task_detail,
    list_data_query_tasks,
)


class DataQueryTaskListResource(Resource):
    def get(self):
        return list_data_query_tasks()


class DataQueryTaskParseResource(Resource):
    def post(self):
        return create_data_query_task(
            file_storage=request.files.get('file'),
            task_name=request.form.get('task_name'),
        )


class DataQueryTaskDetailResource(Resource):
    def get(self, task_id):
        return get_data_query_task_detail(task_id=task_id)

    def delete(self, task_id):
        return delete_data_query_task(task_id=task_id)


class DataQueryTaskGenerateResource(Resource):
    def post(self, task_id):
        payload = request.get_json(silent=True) or {}
        return generate_data_query_export(
            task_id=task_id,
            selected_field_ids=payload.get('selected_field_ids', []),
        )


class DataQueryTaskPreviewResource(Resource):
    def post(self, task_id):
        payload = request.get_json(silent=True) or {}
        return get_data_query_preview(
            task_id=task_id,
            selected_field_ids=payload.get('selected_field_ids', []),
        )

from flask import request, make_response, json, send_file
from flask_restful import Resource
import os
import datetime
import psycopg2
import traceback
from docx import Document
import psycopg2.extras
from authlib.jose import jwt, JoseError
# --- Refactored Imports ---
from config.database import conn_11, conn_13, conn_15
from config.config import BIG_COUNTRY, BOUNDARY_TABLE, CONNECTION_TABLE, FEATURE_OTHER_TABLE

from database.prefix_outage import get_pre_outage_de
from database.as_outage import get_as_outage_de
from database.country_outage import get_country_outage_de
from database.hijack import get_hijack_de
from database.sub_hijack import get_sub_hijack_de
from database.leak_event import get_leak_de

from database.event import get_event_judgeinfo


from utils.get_as_info import get_as_info
from utils.get_event import (get_as_feature, 
                             get_boundary_outage_de,
                             get_boundary_de,
                             get_connection_de,
                             get_event_info,

                             )
from utils.template import (generate_word, 
                            generate_excel, 
                            write_template_1, 
                            write_template_2, 
                            write_template_3)
from utils.data_loader import as_info, prefix_info, domain_info, important_as_dict

SECRET_KEY = 'abcdefghijklmm'
def validate_token(token):
    """用于验证用户注册和用户修改密码或邮箱的token, 并完成相应的确认操作"""
    key = SECRET_KEY
    try:
        data = jwt.decode(token, key)
        print(data)
    except JoseError:
        return 0, 0, 0
    return data['userid'], data['role']

# --- Resource Classes ---
## 都不完整，需要修改
class WordExportResource(Resource):
    """
    导出事件详情为 Word 文档
    Endpoint: /api/v1/reports/word-export
    """
    def post(self):
        detail_url = request.json.get('detail_url')
        detail_url_fields = detail_url.split('/')
        event_type, start_time, problem, event_id, source = detail_url_fields[0], detail_url_fields[1], detail_url_fields[2], detail_url_fields[3], detail_url_fields[4]
        year, month = start_time[0:4], start_time[5:7]
        if event_type == 'prefix_outage':
            table = event_type + '_' + '{}{}'.format(year, month)
            prefix = problem.replace('-', '/')
            row = get_pre_outage_de(conn=conn_15, table=table, prefix=prefix, outage_id=event_id, source=source)
            d = dict()
            asn = ''
            t = ''
            for r in row:
                
                d['outage_prefix'] = prefix
                d['as_of_prefix'] = get_as_info(as_info, r['asn'])
                d['start_time'], d['end_time'] = str(r['s_time']), str(r['e_time'])
                d['event_level'] = r['outage_level']
                d['type_of_as'] = r['as_type']
                d['duration'] = str(r['duration'])
                d['pre_vp_paths'] = r['pre_vp_paths']
                d['eve_vp_paths'] = r['eve_vp_paths']
                d["country"] = r['country']
                d['asn'] = r['asn']
                asn = r['asn']
                t = r['s_time']
            
            event_table = 'event_table_' + '{}{}'.format(year, month)
            detail_url = '{}/{}/{}/{}/{}'.format(event_type, start_time, problem, event_id, source)
            row = get_event_judgeinfo(conn=conn_13, event_table=event_table, detail_url=detail_url)
            for r in row:
                d['state'] = r['state']
                d['judge_reason'] = r['judge_reason']
                d['judge_userid'] = r['judge_userid']
                d['judge_time'] = r['judge_time']


            if BIG_COUNTRY.get(d['country'], False):
                feature_table = 'feature_' + BIG_COUNTRY.get(d['country'])
            else:
                feature_table = FEATURE_OTHER_TABLE
            d['time_list'], d['announ_list'], d['withdraw_list'] = get_as_feature(conn=conn_13, feature_table=feature_table, asn=asn, t=t)
            # 生成word报告
            word_path = generate_word(event_type, d)
            if os.path.exists(word_path):
                return send_file(word_path, as_attachment=True)
            else:
                return {'status': False, 'msg': '生成文件失败或文件不存在'}, 404
        elif event_type == 'as_outage':
            table = event_type + '_' + '{}{}'.format(year, month)
            asn = problem
            row = get_as_outage_de(conn=conn_15, table=table, asn=asn, outage_id=event_id, source=source)
            d = dict()
            t = ''
            for r in row:
                d['outage_as'] = get_as_info(as_info, asn)
                d['name_of_as'] = r['as_name']
                d['country_of_as'] = r['country']
                d['org_of_as'] = r['org_name']
                d['start_time'] = str(r['s_time'])
                d['duration'] = str(r['duration'])
                d['event_level'] = r['outage_level']
                d['name_of_as'] = r['as_name']
                d['as_prefix_num'] = r['total_prefix_num']
                d['outage_prefix_num'] = r['max_outage_prefix_num']
                d['as_type'] = r['as_type']
                d['end_time'] = str(r['e_time'])
                d['end_descr'] = r['outage_level_descr']
                d['pre_vp_paths'] = r['pre_vp_paths']
                d['eve_vp_paths'] = r['eve_vp_paths']
                d['outage_prefixes'] = r['outage_prefixes']
                d['asn'] = asn
                t = r['s_time']
            
            event_table = 'event_table_' + '{}{}'.format(year, month)
            # detail_url = '{}/{}/{}/{}'.format(event_type, start_time, problem, event_id)
            row = get_event_judgeinfo(conn=conn_13, event_table=event_table, detail_url=detail_url)
            for r in row:
                d['state'] = r['state']
                d['judge_reason'] = r['judge_reason']
                d['judge_userid'] = r['judge_userid']
                d['judge_time'] = r['judge_time']

            if BIG_COUNTRY.get(d['country_of_as'], False):
                feature_table = 'feature_' + BIG_COUNTRY.get(d['country'])
            else:
                feature_table = FEATURE_OTHER_TABLE

            d['time_list'], d['announ_list'], d['withdraw_list'] = get_as_feature(conn=conn_13, feature_table=feature_table, asn=asn, t=t)
            # 生成word报告
            word_path = generate_word(event_type, d)
            if os.path.exists(word_path):
                return send_file(word_path, as_attachment=True)
            else:
                return {'status': False, 'msg': '生成文件失败或文件不存在'}, 404
        elif event_type == 'country_outage':
            table = event_type + '_' + '{}{}'.format(year, month)
            country = problem
            row = get_country_outage_de(conn=conn_15, table=table, country=country, outage_id=event_id, source=source)
            d = dict()
            for r in row:
                d['outage_country'] = r['country_chinese_name']
                d['total_as_num'] = r['total_as_num']
                d['start_time'] = str(r['s_time'])
                d['end_time'] = str(r['e_time'])
                d['duration'] = str(r['duration'])
                d['event_level'] = r['outage_level']
                d['outage_as_num'] = r['max_outage_as_num']
                d['event_descr'] = r['outage_level_descr']
                d['outage_ases'] = r['outage_ases']
            
            event_table = 'event_table_' + '{}{}'.format(year, month)
            # detail_url = '{}/{}/{}/{}'.format(event_type, start_time, problem, event_id)
            row = get_event_judgeinfo(conn=conn_13, event_table=event_table, detail_url=detail_url)
            for r in row:
                d['state'] = r['state']
                d['judge_reason'] = r['judge_reason']
                d['judge_userid'] = r['judge_userid']
                d['judge_time'] = r['judge_time']
            # 国家中断不展示特征
            # 生成word报告
            word_path = generate_word(event_type, d)
            if os.path.exists(word_path):
                return send_file(word_path, as_attachment=True)
            else:
                return {'status': False, 'msg': '生成文件失败或文件不存在'}, 404
        elif event_type == 'hijack':
            table = event_type + '_' + '{}{}'.format(year, month)
            prefix = problem.replace('-', '/')
            row = get_hijack_de(conn=conn_11, table=table, prefix=prefix, hijack_id=event_id, source=source)
            d = dict()
            hijacked_as = ''
            t = ''
            for r in row:
                d['hijacked_prefix'] = prefix
                asn = r['hijacked_as']
                d['hijacked_as'] = get_as_info(as_info, asn)
                d['hijacked_asn'] = r['hijacked_as']
                asn = r['hijacker_as']
                d['hijacker_as'] = get_as_info(as_info, asn)
                d['hijacker_asn'] = r['hijacker_as']
                d['start_time'] = str(r['s_time'])
                d['end_time'] = str(r['e_time'])
                d['duration'] = str(r['duration'])
                d['event_descr'] = str(r['hijack_level_info'])
                d['event_level'] = r['hijack_level']
                d['pre_vp_paths'] = r['pre_vp_paths']
                d['eve_vp_paths'] = r['eve_vp_paths']
                hijacked_as = r['hijacked_as']
                t = r['s_time']
            
            event_table = 'event_table_' + '{}{}'.format(year, month)
            # detail_url = '{}/{}/{}/{}'.format(event_type, start_time, problem, event_id)
            row = get_event_judgeinfo(conn=conn_13, event_table=event_table, detail_url=detail_url)
            for r in row:
                d['state'] = r['state']
                d['judge_reason'] = r['judge_reason']
                d['judge_userid'] = r['judge_userid']
                d['judge_time'] = r['judge_time']

            
            country = as_info.get(hijacked_as, {}).get('country', '')
            if BIG_COUNTRY.get(country, False):
                feature_table = 'feature_' + BIG_COUNTRY.get(country)
            else:
                feature_table = FEATURE_OTHER_TABLE
            d['time_list'], d['announ_list'], d['withdraw_list'] = get_as_feature(conn=conn_13, feature_table=feature_table, asn=hijacked_as, t=t)
            # 生成word报告
            word_path = generate_word(event_type, d)
            if os.path.exists(word_path):
                return send_file(word_path, as_attachment=True)
            else:
                return {'status': False, 'msg': '生成文件失败或文件不存在'}, 404
        elif event_type == 'sub_hijack':
            table = event_type + '_' + '{}{}'.format(year, month)
            prefix = problem.replace('-', '/')
            row = get_sub_hijack_de(conn=conn_11, table=table, prefix=prefix, sub_hijack_id=event_id, source=source)
            d = dict()
            hijacked_as = ''
            t = ''
            for r in row:
                d['hijacker_prefix'] = prefix
                d['hijacked_prefix'] = r['hijacked_prefix']
                asn = r['hijacked_as']
                d['hijacked_as'] = get_as_info(as_info, asn)
                d['hijacked_asn'] = r['hijacked_as']
                asn = r['hijacker_as']
                d['hijacker_as'] = get_as_info(as_info, asn)
                d['hijacker_asn'] = r['hijacker_as']
                d['start_time'] = str(r['s_time'])
                d['end_time'] = str(r['e_time'])
                d['duration'] = str(r['duration'])
                d['event_level'] = r['sub_hijack_level']
                d['event_descr'] = r['level_info']
                hijacked_as = str(r['hijacked_as'])
                t = r['s_time']

            event_table = 'event_table_' + '{}{}'.format(year, month)
            # detail_url = '{}/{}/{}/{}'.format(event_type, start_time, problem, event_id)
            row = get_event_judgeinfo(conn=conn_13, event_table=event_table, detail_url=detail_url)
            for r in row:
                d['state'] = r['state']
                d['judge_reason'] = r['judge_reason']
                d['judge_userid'] = r['judge_userid']
                d['judge_time'] = r['judge_time']
            # d['time_list'], d['announ_list'], d['withdraw_list'] = get_as_feature(conn=conn_13, feature_table=FEATURE_TABLE, asn=hijacked_as, t=t)
            # 生成word报告
            word_path = generate_word(event_type, d)
            if os.path.exists(word_path):
                return send_file(word_path, as_attachment=True)
            else:
                return {'status': False, 'msg': '生成文件失败或文件不存在'}, 404
        elif event_type == 'leak':
            table = 'leak_event_' + '{}{}'.format(year, month)
            prefix = problem.replace('-', '/')
            row = get_leak_de(conn=conn_13, table=table, prefix=prefix, leak_id=event_id, source=source)
            d = dict()
            asn = ''
            t = ''
            for r in row:
                d['leak_prefix'] = prefix
                d['prefix_origin_as'] = r['prefix_ori_as']
                asn = r['leak_to']
                d['leak_to_as'] = get_as_info(as_info, asn)
                asn = r['leak_by']
                d['leak_by_as'] = get_as_info(as_info, asn)
                d['leak_to_country'] = r['leak_to_country']
                d['leak_by_country'] = r['leak_by_country']
                d['event_descr'] = r['leak_level_info']
                d['as_path'] = r['as_path']
                d['event_level'] = r['leak_level']
                d['event_time'] = str(r['s_time'])
                d['leak_by'] = r['leak_by']
                d['leak_to'] = r['leak_to']
                asn = r['leak_to']
                t = r['s_time']

            event_table = 'event_table_' + '{}{}'.format(year, month)
            # detail_url = '{}/{}/{}/{}'.format(event_type, start_time, problem, event_id)
            row = get_event_judgeinfo(conn=conn_13, event_table=event_table, detail_url=detail_url)
            for r in row:
                d['state'] = r['state']
                d['judge_reason'] = r['judge_reason']
                d['judge_userid'] = r['judge_userid']
                d['judge_time'] = r['judge_time']


            country = as_info.get(asn, {}).get('country', '')
            if BIG_COUNTRY.get(country, False):
                feature_table = 'feature_' + BIG_COUNTRY.get(country)
            else:
                feature_table = FEATURE_OTHER_TABLE

            d['time_list'], d['announ_list'], d['withdraw_list'] = get_as_feature(conn=conn_13, feature_table=feature_table, asn=asn, t=t)
            # 生成word报告
            word_path = generate_word(event_type, d)
            if os.path.exists(word_path):
                return send_file(word_path, as_attachment=True)
            else:
                return {'status': False, 'msg': '生成文件失败或文件不存在'}, 404
        elif event_type == 'boundary_outage':
            table = 'boundary_outage_' + '{}{}'.format(year, month)
            export_as = problem.split('--')[0]
            peer_as = problem.split('--')[1]
            row = get_boundary_outage_de(conn=conn_13, table=table, export_as=export_as, 
                                        peer_as=peer_as, outage_id=event_id, source=source)
            d = dict()
            asn = ''
            t = ''
            for r in row:
                d['export_as_info'] = get_as_info(as_info, export_as)
                d['peer_as_info'] = get_as_info(as_info, peer_as)
                d['start_time'] = str(r['s_time'])
                d['duration'] = str(r['duration'])
                d['end_time'] = str(r['e_time'])
                d['event_level'] = r['outage_level']
                d['max_used_count'] = r['max_used_count']
                d['export_as_type'] = r['export_as_type']
                d['peer_as_type'] = r['peer_as_type']
                d['outage_level_descr'] = r['outage_level_descr']

            event_table = 'event_table_' + '{}{}'.format(year, month)
            # detail_url = '{}/{}/{}/{}'.format(event_type, start_time, problem, event_id)
            row = get_event_judgeinfo(conn=conn_13, event_table=event_table, detail_url=detail_url)
            for r in row:
                d['state'] = r['state']
                d['judge_reason'] = r['judge_reason']
                d['judge_userid'] = r['judge_userid']
                d['judge_time'] = r['judge_time']

            country = as_info.get(export_as, {}).get('country', '')
            if BIG_COUNTRY.get(country, False):
                feature_table = 'feature_' + BIG_COUNTRY.get(country)
            else:
                feature_table = FEATURE_OTHER_TABLE

            d['time_list'], d['announ_list'], d['withdraw_list'] = get_as_feature(conn=conn_13, feature_table=feature_table, asn=export_as, t=t)
            # 生成word报告
            word_path = generate_word(event_type, d)
            if os.path.exists(word_path):
                return send_file(word_path, as_attachment=True)
            else:
                return {'status': False, 'msg': '生成文件失败或文件不存在'}, 404
        

class ExcelExportResource(Resource):
    """
    将事件列表导出为 Excel
    Endpoint: /api/v1/reports/excel-export
    """
    def post(self):
        try:
            token = request.headers['Authorization']
            userid, role = validate_token(token=token)
            if role not in ['admin', 'operator']:
                return json.dumps({'status': False, 'msg': '没有操作权限！'}, ensure_ascii=False)
        except:
            return json.dumps({'status': False, 'msg': '未登录！'}, ensure_ascii=False)
        detail_url = request.json.get('detail_url')
        state = request.json.get('state')
        judge_reason = request.json.get('judge_reason')
        judge_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        judge_userid = userid

        date = str(detail_url.split('/')[1])[0:7].replace('-', '')
        print(date)
        event_table = 'event_table_' + date

        try:
            cursor = conn_11.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cursor.execute("SELECT username FROM users WHERE userid = %s", (judge_userid,))
            judge_username = cursor.fetchone()['username']
            sql = """
                    UPDATE {} SET state = '{}', judge_reason = '{}', judge_userid = '{}',
                    judge_username = '{}', judge_time = '{}'
                    WHERE detail_url = '{}'
                    """.format(event_table, state, judge_reason, judge_userid, 
                            judge_username, judge_time, detail_url)
            cursor.execute(sql)
            conn_11.commit()
            cursor.close()
        except Exception as e:
            #抛出错误信息
            traceback.print_exc()
            # 如果发生错误则回滚
            conn_11.rollback()
            return json.dumps({'status': False, 'msg': '研判操作失败！错误为：{}'.format(e)}, ensure_ascii=False)
        
        return json.dumps({'status': True, 'msg': '事件研判成功！'}, ensure_ascii=False)

class ExcelExportCountryResource(Resource):
    """
    导出国家边界或连通数据为 Excel
    Endpoint: /api/v1/reports/excel-export/country
    """
    def post(self):
        # Original logic from export_excel_country()
        typee = request.json.get('type')
        rows = request.json.get('rows')
        if typee == 'border':
            data_list = list()
            for row in rows:
                boundary_table = BOUNDARY_TABLE
                export_as = row.split('-')[0]
                peer_as = row.split('-')[1]
                row = get_boundary_de(conn=conn_11, boundary_table=boundary_table, 
                                    export_as=export_as, peer_as=peer_as)
                d = dict()
                for r in row:
                    d['起始国AS'] = r['export_as']
                    d['起始国AS国家'] = r['export_as_country']
                    d['起始国AS名称'] = r['export_as_name']
                    d['起始国AS所属机构'] = r['export_as_org']
                    d['起始国AS类型'] = r['export_as_type']
                    d['边界国AS'] = r['peer_as']
                    d['边界国AS国家'] = r['peer_as_country']
                    d['边界国AS名称'] = r['peer_as_name']
                    d['边界国AS所属机构'] = r['peer_as_org']
                    d['边界国AS类型'] = r['peer_as_type']
                data_list.append(d)
            excel_path = generate_excel(state=typee, data_list=data_list)
            if os.path.exists(excel_path):
                return send_file(excel_path, as_attachment=True)
            else:
                return {'status': False, 'msg': '生成Excel文件失败'}, 404
        elif typee == 'connect':
            data_list = list()
            for row in rows:
                connection_table = CONNECTION_TABLE
                vp_country = row.split('-')[0]
                dst_country = row.split('-')[1]
                row = get_connection_de(conn=conn_11, connection_table=connection_table, 
                                        vp_country=vp_country, dst_country=dst_country)
                d = dict()
                for r in row:
                    d['起始国'] = r['vp_country_chinese_name']
                    d['目的国'] = r['dst_country_chinese_name']
                    d['出口AS数量'] = r['export_as_count']
                    d['入口AS数量'] = r['entrance_as_count']
                    d['AS路径数量'] = r['path_count']
                    d['关键路径数量'] = r['key_path_count']
                    d['机构级关键路径数量'] = r['key_org_path_count']
                    d['国家级关键路径数量'] = r['key_country_path_count']
                data_list.append(d)
            excel_path = generate_excel(state=typee, data_list=data_list)
            if os.path.exists(excel_path):
                return send_file(excel_path, as_attachment=True)
            else:
                return {'status': False, 'msg': '生成Excel文件失败'}, 404

class TemplateExportResource(Resource):
    """
    根据类型导出模板文件
    Endpoint: /api/v1/reports/template-export
    """
    def post(self):
        detail_url = request.json.get('detail_url')
        templateType = request.json.get('templateType')   # default   types 
        print(f"Generating templates for {detail_url}, type: {templateType}")
        event_type, d = get_event_info(detail_url=detail_url, conn=conn_11)

        # 生成应急处置模板
        print('generate_template函数执行')
        if not os.path.exists('reports'):
            os.makedirs('reports')
        doc = Document()
        # write_template_1(doc, event_type, d, as_info, prefix_info, domain_info)
        

        # List to store all generated PDF paths
        # generated_pdfs = []

        # Generate templates based on selected types
        # for template_type in types:
        doc = Document()
        
        if templateType == 'type1':
            # 应急处置模板
            write_template_1(doc, event_type, d, as_info, prefix_info, domain_info, important_as_dict)
            template_name = f"{event_type}_type1"
        elif templateType == 'type2':
            # 事件分析模板
            write_template_2(doc, event_type, d, as_info, prefix_info, domain_info, important_as_dict)
            template_name = f"{event_type}_type2"
        elif templateType == 'type3':
            # 安全报告模板
            write_template_3(doc, event_type, d, as_info, prefix_info, domain_info, important_as_dict)
            template_name = f"{event_type}_type3"
        else:
            pass

        # Save Word document
        docx_path = os.path.abspath(f'reports/{template_name}.docx')
        doc.save(docx_path)
        print(docx_path)
        
        # Convert to PDF
        # os.system(command=f'soffice --headless --invisible --convert-to pdf {docx_path} --outdir {os.path.abspath("reports/")}')
        # pdf_path = os.path.abspath(f'reports/{template_name}.pdf')
        # print(pdf_path)
        
        if os.path.exists(docx_path):
            # Format the path for response
            formatted_path = docx_path.replace('/', '-') + '-' + datetime.datetime.now().strftime('%H:%m:%S')
            return make_response(formatted_path)
        else:
            return make_response("Failed to generate template", 500)
        
class FileDownloadResource(Resource):
    """
    根据 URL 下载文件
    Endpoint: /api/v1/reports/download/<path:download_url>
    """
    def get(self, download_url):
        safe_base_dir = os.path.abspath('reports/')
        
        # 修正：更健壮地处理被污染的URL，分离出文件名和时间戳
        try:
            # 移除前端错误拼接导致的前缀
            if download_url.startswith('-home-bgpdata-Domeye-backend-reports-'):
                 download_url = download_url.split('reports-')[-1]

            # 分离真正的文件名和末尾的时间戳
            # 例如: hijack_type2.docx-15:07:48
            parts = download_url.rsplit('-', 1)
            file_name = parts[0].replace('-', '/')
            if len(parts) > 1 and ':' in parts[1]:
                # 这是一个有效的时间戳，我们只需要文件名部分
                pass
        except Exception:
            # 如果分割失败，说明路径格式不符合预期，直接使用原始路径（可能导致错误，但保持兼容）
            file_name = download_url.replace('-', '/')

        target_file = os.path.abspath(os.path.join(safe_base_dir, file_name))
        
        # 确保请求的文件在安全目录内
        if not target_file.startswith(safe_base_dir):
            return {'status': False, 'msg': '禁止访问的路径'}, 403
        if os.path.exists(target_file):
            return send_file(target_file, as_attachment=True)
        else:
            return {'status': False, 'msg': f'文件未找到: {target_file}'}, 404 


# ========== 国家中断报告导出功能 ==========

from utils.report_data import build_country_outage_report_data, generate_country_outage_word


class CountryOutageReportDataResource(Resource):
    """
    获取国家中断报告数据接口
    GET /api/v1/reports/country-outage-data/<country>/<start_time>/<event_id>/<source>
    返回报告所需的全部数据（包括基线、当前值、AS明细、前缀明细等）
    """
    def get(self, country, start_time, event_id, source):
        try:
            report_data = build_country_outage_report_data(
                conn_event=conn_15,
                conn_feature=conn_11,
                country=country,
                start_time=start_time,
                event_id=event_id,
                source=source,
                as_info=as_info
            )
            return report_data, 200
        except Exception as e:
            traceback.print_exc()
            return {'status': False, 'msg': f'获取报告数据失败: {str(e)}'}, 500


class CountryOutageReportExportResource(Resource):
    """
    导出国家中断报告（Word格式）
    GET /api/v1/reports/country-outage-export/<country>/<start_time>/<event_id>/<source>
    返回 Word 文件下载
    """
    def get(self, country, start_time, event_id, source):
        try:
            # 获取报告数据
            report_data = build_country_outage_report_data(
                conn_event=conn_15,
                conn_feature=conn_11,
                country=country,
                start_time=start_time,
                event_id=event_id,
                source=source,
                as_info=as_info
            )
            
            if not report_data or report_data.get('status') == False:
                return {'status': False, 'msg': '获取报告数据失败'}, 500
            
            # 生成 Word 文档
            file_path = generate_country_outage_word(report_data, as_info)
            
            if not file_path or not os.path.exists(file_path):
                return {'status': False, 'msg': '生成报告文件失败'}, 500
            
            # 返回文件下载
            country_name = report_data.get('meta', {}).get('country_chinese_name', country)
            filename = f"{country_name}路由中断事件报告_{start_time[:10]}.docx"
            
            return send_file(
                file_path,
                mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                as_attachment=True,
                download_name=filename
            )
            
        except Exception as e:
            traceback.print_exc()
            return {'status': False, 'msg': f'导出报告失败: {str(e)}'}, 500

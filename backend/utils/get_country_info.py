"""
获取country_info中的信息
"""

def get_country_english_short_name(country_info_dict: dict, two_letter_code: str):
    """
    返回国家的英文简称
    :param country_info_dict: 国家信息字典
    :param two_letter_code: 国家两字母简称
    :return: 国家的英文简称
    """
    if two_letter_code is None:
        return None
    if two_letter_code in country_info_dict:
        if country_info_dict[two_letter_code].get('english_short_name') != '':
            return country_info_dict[two_letter_code].get('english_short_name')
    return None


def get_country_english_full_name(country_info_dict: dict, two_letter_code: str):
    """
    返回国家的英文全称
    :param country_info_dict: 国家信息字典
    :param two_letter_code: 国家两字母简称
    :return: 国家的英文全称
    """
    if two_letter_code is None:
        return None
    if two_letter_code in country_info_dict:
        if country_info_dict[two_letter_code].get('english_full_name') != '':
            return country_info_dict[two_letter_code].get('english_full_name')
    return None


def get_country_chinese_name(country_info_dict: dict, two_letter_code: str):
    """
    返回国家的中文简称
    :param country_info_dict: 国家信息字典
    :param two_letter_code: 国家两字母简称
    :return: 国家的中文简称
    """
    if two_letter_code is None:
        return None
    if two_letter_code in country_info_dict:
        if country_info_dict[two_letter_code].get('chinese_short_name') != '':
            return country_info_dict[two_letter_code].get('chinese_short_name')
    if two_letter_code == 'EU':
        return '欧盟'
    return two_letter_code


def get_country_longitude(country_info_dict: dict, two_letter_code: str):
    """
    返回国家的经度
    :param country_info_dict: 国家信息字典
    :param two_letter_code: 国家两字母简称
    :return: 国家的经度
    """
    if two_letter_code is None:
        return None
    if two_letter_code in country_info_dict:
        if country_info_dict[two_letter_code].get('longitude') != '':
            return country_info_dict[two_letter_code].get('longitude')
    return None


def get_country_latitude(country_info_dict: dict, two_letter_code: str):
    """
    返回国家的纬度
    :param country_info_dict: 国家信息字典
    :param two_letter_code: 国家两字母简称
    :return: 国家的纬度
    """
    if two_letter_code is None:
        return None
    if two_letter_code in country_info_dict:
        if country_info_dict[two_letter_code].get('latitude') != '':
            return country_info_dict[two_letter_code].get('latitude')
    return None


def get_country_digital_code(country_info_dict: dict, two_letter_code: str):
    """
    返回国家的数字代码
    :param country_info_dict: 国家信息字典
    :param two_letter_code: 国家两字母简称
    :return: 国家的数字代码
    """
    if two_letter_code is None:
        return None
    if two_letter_code in country_info_dict:
        if country_info_dict[two_letter_code].get('digital_code') != '':
            return country_info_dict[two_letter_code].get('digital_code')
    return None

def get_country_two_letter_code(country_info_dict: dict, chinese_name: str):
    """
    返回国家的中文简称
    :param country_info_dict: 国家信息字典
    :param two_letter_code: 国家两字母简称
    :return: 国家的中文简称
    """
    if chinese_name is None:
        return None
    if chinese_name in country_info_dict:
        if country_info_dict[chinese_name].get('two_letter_code') != '':
            return country_info_dict[chinese_name].get('two_letter_code')
    if chinese_name == '欧盟':
        return 'EU'
    return chinese_name
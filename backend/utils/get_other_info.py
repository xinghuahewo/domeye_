def get_as_importance(important_as_dict, asn):
    """
    判断asn是否为重要asn
    :param important_as_dict: 重要asn信息字典
    :param asn: asn
    :return: 是否为重要asn
    """
    if '_' in asn:
        public_as = asn.split('_')[0]
        if important_as_dict.get(int(public_as)) is not None:
            return True
    else:
        if important_as_dict.get(int(asn)) is not None:
            return True
    return False



def get_leak_triplet(triplet_info: dict, first_as: str, second_as: str, third_as: str):
    """
    获取leak triplet稳定度信息
    """
    if first_as in triplet_info and second_as in triplet_info[first_as] and third_as in triplet_info[first_as][second_as]:
        return triplet_info[first_as][second_as][third_as]['stability']
    else:
        return 0


def get_private_as_city(private_as_dict: dict, asn: str):
    """
    返回私有AS的分布城市
    :param private_as_dict: 私有AS信息字典
    :param asn: 格式为"公有AS_私有AS"
    :return: 私有AS的分布城市
    """
    if '_' in asn:
        public_as = asn.split('_')[0]
        private_as = asn.split('_')[1]
        try:
            city = private_as_dict[public_as][private_as]['city']
        except:
            city = 'not found'
        if city in ['', 'NULL', 'None']:
            city =  'not found'
        return city
    else:
        return None
declare type LeftTopListDataType = {
    // 统计名称
    title: string,
    // 统计数量
    num: number,
    // 变化类型 true: 增加
    changeType: boolean,
    // 变化幅度
    changeNum: number
}

// 左上数据
declare type LeftTopDataType = LeftTopListDataType[]



declare type LeftCenterCountryData = {
    // 国旗 二字码
    countryFlage: string,
    // 国家名称
    countryName: string,
    // 出口路径数量
    num: number,
}

declare type LeftCenterOrganizationData = {
    // 出口路径数量
    value: number,
    // 出口机构
    name: string,
}

declare type LeftCenterASData = {
    // 出口路径数量
    value: number,
    // 出口AS
    name: string,
}

// 左中数据
declare type LeftCenterData = {
    // 按终点国家聚合出口路径数量
    country: LeftCenterCountryData[],
    // 按出口机构聚合出口路径数量
    organization: LeftCenterOrganizationData[],
    // 按出口AS聚合出口路径数量
    as: LeftCenterASData[]
};



declare type LeftBottomOrganizationData = {
    // 机构名称
    organization: string,
    // 路径数量
    path: number
}

declare type LeftBottomAreaData = {
    // 绕道路径起点AS名称
    as: string,
    // 绕道经过地区国旗 flag-icon-二字码
    countryFlag: string,
    // 绕道经过地区国家名称
    countryName: string,
    // 路径数量
    path: number
}

// 左下数据
declare type LeftBottomData = {
    // 以境内机构进行统计计算绕道路径数量
    organization: LeftBottomOrganizationData[],
    // 以绕道路径经过的国家进行统计
    area: LeftBottomAreaData[],
}



declare type CenterTopListData = {
    // 起始点地区名称
    ExportCountry: string,
    // 起始点地区经纬度
    ExportLng: number,
    ExportLat: number,
    // 终点地区名称
    ImportCountry: string,
    // 终点地区经纬度
    ImportLng: number,
    ImportLat: number,
    // 路径数量
    t: number
    // 标签
    label: number,
}

// 中上数据
declare type CenterTopData = CenterTopListData[]



declare type CenterBottomRankData = {
    // 去往的终点国家国旗 flag-icon-二字码
    EndCountryFlag: string,
    // 去往的终点国家名称
    EndCountryName: string,
    // 关键路径的AS名称
    as: string,
    // 关键路径的AS所属国家国旗 flag-icon-二字码
    BelongCountryFlag: string,
    // 关键路径的AS所属国家名
    BelongCountryName: string,
    // 霸权值
    SupremacyNum: 1,
    // 路径数量
    PathNum: 1
}

// 中下数据 - 从观测地区出发的到其他国家的关键路径
declare type CenterBottomData = {
    // 霸权值排行数据
    SupremacyRank: CenterBottomRankData[],
    // 路径数量排行数据
    PathRank: CenterBottomRankData[]
}



declare type RightTopListData = {
    // 统计名称
    title: string,
    // 统计数量
    num: number,
    // 变化类型 true: 增加
    changeType: boolean,
    // 变化幅度
    changeNum: number
}

// 右上数据
declare type RightTopData = RightTopListData[]



declare type RightCenterCountryData = {
    // 国旗 二字码
    countryFlage: string,
    // 国家名称
    countryName: string,
    // 出口路径数量
    num: number,
}

declare type RightCenterOrganizationData = {
    // 出口路径数量
    value: number,
    // 出口机构
    name: string,
}

declare type RightCenterASData = {
    // 出口路径数量
    value: number,
    // 出口AS
    name: string,
}

// 右中数据
declare type RightCenterData = {
    // 以观测地区为终点国家聚合从联通地区出发到观测地区的入口路径数量
    country: RightCenterCountryData[],
    // 按入口终点机构聚合入口路径数量
    organization: RightCenterOrganizationData[],
    // 按入口终点AS聚合入口路径数量
    as: RightCenterASData[]
}



declare type RightBottomOrganizationData = {
    // 借道地区国旗 flag-icon-二字码
    countryFlag: string,
    // 借道地区国名
    countryName: string,
    // 机构名称
    organization: string,
    // 路径数量
    path: number
}

declare type RightBottomAreaData = {
    // 借道地区国旗 flag-icon-二字码
    countryFlag: string,
    // 借道地区国家名称
    countryName: string,
    // 借道地区终点地区国旗 flag-icon-二字码
    countryEndFlag: string,
    // 借道地区经过地区国家名称
    countryEndName: string,
    // 路径数量
    path: number
}

// 右下数据 - 从联通地区出发但终点不是观测地区但是又经过了观测地区的路径
declare type RightBottomData = {
    // 路径经过观测地区机构的次数
    organization: RightBottomOrganizationData[],
    // 以起点和终点进行聚合计算路径通过观测地区的路径数量
    area: RightBottomAreaData[],
}

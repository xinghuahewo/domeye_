import type { DerivedNumericFact } from '../domain/contracts.js'
import {
  computeCountryOutageSkillBundleSha256,
  defaultCountryOutageSkillPath,
} from '../pi/country-outage-skill-bundle.js'
import type {
  CountryOutageReportDraft,
  EvidenceParagraph,
  NarrationRequest,
  ReportEvidenceBundle,
  ReportNarrator,
  ReportSection,
} from './contracts.js'
import { COUNTRY_OUTAGE_REPORT_VALIDATOR_RULES_VERSION } from './draft-validator.js'
import {
  extremaPoint,
  formatDurationMinutes,
  formatInteger,
  formatPercent,
  localDateTimeLabel,
  localTimeLabel,
} from './format.js'

function paragraph(text: string, ...evidenceRefs: string[]): EvidenceParagraph {
  return { text, evidenceRefs }
}

function derived(
  values: DerivedNumericFact[],
  metric: string,
): DerivedNumericFact {
  const value = values.find((item) => item.metric === metric)
  if (!value) throw new Error(`缺少派生事实：${metric}`)
  return value
}

export function buildDeterministicCountryOutageDraft(
  evidence: ReportEvidenceBundle,
): CountryOutageReportDraft {
    const { facts, asnPages } = evidence
    const points = Object.fromEntries(
      facts.keyVisibilityPoints.map((item) => [item.kind, item]),
    )
    const start = points.start
    const lowest = points.lowest
    const end = points.end
    const largestDrop = points.largest_drop
    const largestRecovery = points.largest_recovery
    if (!start || !lowest || !end) {
      throw new Error('正式报告缺少起点、最低点或结束点')
    }

    const loss = derived(
      facts.derivedFacts,
      'start_to_lowest_visible_prefix_vp_change',
    )
    const lossRatio = derived(
      facts.derivedFacts,
      'start_to_lowest_loss_ratio',
    )
    const endGap = derived(facts.derivedFacts, 'end_gap_from_start')
    const recovered = derived(facts.derivedFacts, 'recovered_from_lowest')
    const recoveryShare = derived(
      facts.derivedFacts,
      'recovery_share_of_prior_loss',
    )
    const country = facts.event.country_name
    const trendClaimParagraphs = (
      facts.trendProduct?.evidence_graph.nodes ?? []
    ).flatMap((node, index) => (
      node.node_type === 'Claim' && typeof node.text === 'string'
        ? [paragraph(node.text, `trend:/nodes/${index}`)]
        : []
    ))
    const windowStart = localDateTimeLabel(facts.scope.window_start_local)
    const windowEnd = localTimeLabel(facts.scope.window_end_local)
    const minRef = lowest.provenance.endpoint + ':' + lowest.provenance.pointer
    const startRef = start.provenance.endpoint + ':' + start.provenance.pointer
    const endRef = end.provenance.endpoint + ':' + end.provenance.pointer
    const largestDropSlot = largestDrop
      ? facts.series[largestDrop.slotIndex]
      : undefined
    const largestDropDelta =
      typeof largestDropSlot?.visible_prefix_vp_delta === 'number'
        ? largestDropSlot.visible_prefix_vp_delta
        : undefined
    const startToLowestDirection =
      loss.value > 0 ? '下降' : loss.value < 0 ? '上升' : '持平'
    const lowestToEndDirection =
      recovered.value > 0
        ? '回升'
        : recovered.value < 0
          ? '下降'
          : '持平'
    const endRelativeToStart =
      endGap.value > 0
        ? '低于'
        : endGap.value < 0
          ? '高于'
          : '持平'
    const snapshotBoundaryParagraph = facts.snapshot.isFinal
      ? `本报告只描述 RRC25 的 BGP 控制面。数据截止为 ${facts.snapshot.dataThrough ?? '当前发布窗口'}，不能仅凭这些数据判断用户能否上网、具体业务影响、事件原因或责任主体。`
      : `本报告只描述 RRC25 的 BGP 控制面。当前发布仍为非最终状态，数据截至 ${facts.snapshot.dataThrough ?? '当前发布窗口'}；观测窗口之外以及数据截止点之后的状态未知，不能仅凭这些数据判断用户能否上网、具体业务影响、事件原因或责任主体。`
    const startToLowestKeyNumbers =
      loss.value > 0
        ? `窗口覆盖率从起点 ${formatPercent(
            start.visiblePrefixVpRatio,
          )} 降至最低 ${formatPercent(
            lowest.visiblePrefixVpRatio,
          )}，结束时为 ${formatPercent(
            end.visiblePrefixVpRatio,
          )}。最低点相对起点减少 ${formatInteger(
            loss.value,
          )} 条关系，占起点可见关系的 ${formatPercent(lossRatio.value)}。`
        : loss.value < 0
          ? `窗口覆盖率从起点 ${formatPercent(
              start.visiblePrefixVpRatio,
            )} 升至最低点 ${formatPercent(
              lowest.visiblePrefixVpRatio,
            )}，结束时为 ${formatPercent(
              end.visiblePrefixVpRatio,
            )}。最低点相对起点增加 ${formatInteger(
              Math.abs(loss.value),
            )} 条关系，变化占起点可见关系的 ${formatPercent(
              Math.abs(lossRatio.value),
            )}。`
          : `起点至最低点覆盖率持平，均为 ${formatPercent(
              start.visiblePrefixVpRatio,
            )}，结束时为 ${formatPercent(
              end.visiblePrefixVpRatio,
            )}。起点至最低点可见关系差值为 ${formatInteger(
              loss.value,
            )} 条，占起点可见关系的 ${formatPercent(lossRatio.value)}。`
    const visibilityFirstParagraph =
      loss.value > 0
        ? `${localTimeLabel(start.observedAtLocal)}，${formatInteger(
            start.visiblePrefixVpCount,
          )} 条固定路由观测关系可见，覆盖率为 ${formatPercent(
            start.visiblePrefixVpRatio,
          )}。${localTimeLabel(lowest.observedAtLocal)} 可见关系降至 ${formatInteger(
            lowest.visiblePrefixVpCount,
          )} 条，成为窗口最低点。`
        : loss.value < 0
          ? `${localTimeLabel(start.observedAtLocal)}，${formatInteger(
              start.visiblePrefixVpCount,
            )} 条固定路由观测关系可见，覆盖率为 ${formatPercent(
              start.visiblePrefixVpRatio,
            )}。${localTimeLabel(lowest.observedAtLocal)} 可见关系升至 ${formatInteger(
              lowest.visiblePrefixVpCount,
            )} 条；按冻结派生事实，起点至最低点方向为上升。`
          : `${localTimeLabel(start.observedAtLocal)}，${formatInteger(
              start.visiblePrefixVpCount,
            )} 条固定路由观测关系可见，覆盖率为 ${formatPercent(
              start.visiblePrefixVpRatio,
            )}。${localTimeLabel(lowest.observedAtLocal)} 可见关系仍为 ${formatInteger(
              lowest.visiblePrefixVpCount,
            )} 条，起点至最低点保持持平。`
    const visibilitySecondParagraph =
      loss.value > 0
        ? `从起点到最低点共减少 ${formatInteger(
            loss.value,
          )} 条可见关系。这说明窗口内发生了大范围的路由传播覆盖下降，但不能由控制面关系数量推算实际用户影响。`
        : loss.value < 0
          ? `从起点到最低点共增加 ${formatInteger(
              Math.abs(loss.value),
            )} 条可见关系。起点至最低点的路由传播覆盖上升，但不能由控制面关系数量推算实际用户影响。`
          : `从起点到最低点的可见关系差值为 ${formatInteger(
              loss.value,
            )} 条，路由传播覆盖保持持平；这仍不能由控制面关系数量推算实际用户影响。`
    const endComparedWithStartSentence =
      endGap.value > 0
        ? `但结束时仍比起点少 ${formatInteger(
            endGap.value,
          )} 条，覆盖率仍低于起点。`
        : endGap.value < 0
          ? `窗口结束比起点多 ${formatInteger(
              Math.abs(endGap.value),
            )} 条，覆盖率高于起点。`
          : `窗口结束与起点持平，可见关系差值为 ${formatInteger(
              endGap.value,
            )} 条。`
    const recoveryUsesShare =
      recovered.value > 0 &&
      loss.value > 0 &&
      recoveryShare.value >= 0 &&
      recoveryShare.value <= 1
    const lowestToEndSentence =
      recovered.value > 0
        ? recoveryUsesShare
          ? `${localTimeLabel(lowest.observedAtLocal)}最低点之后，可见关系到窗口结束回升 ${formatInteger(
              recovered.value,
            )} 条，相当于此前损失的 ${formatPercent(
              recoveryShare.value,
              1,
            )}。`
          : `${localTimeLabel(lowest.observedAtLocal)}最低点之后，可见关系到窗口结束回升 ${formatInteger(
              recovered.value,
            )} 条。`
        : recovered.value < 0
          ? `${localTimeLabel(lowest.observedAtLocal)}最低点之后，可见关系到窗口结束继续下降 ${formatInteger(
              Math.abs(recovered.value),
            )} 条。`
          : `${localTimeLabel(lowest.observedAtLocal)}最低点之后，可见关系到窗口结束保持不变，差值为 ${formatInteger(
              recovered.value,
            )} 条。`
    const lowestToEndSummary =
      recovered.value > 0
        ? '窗口后段虽有回升'
        : recovered.value < 0
          ? '最低点至窗口结束继续下降'
          : '最低点至窗口结束保持不变'
    const endRelativeSummary =
      endGap.value > 0
        ? '但结束时仍低于起点'
        : endGap.value < 0
          ? '结束时高于起点'
          : '结束时与起点持平'
    const endStateSecondParagraph =
      recovered.value > 0 && endGap.value > 0
        ? '更准确的说法是窗口后段出现部分回升。截至窗口结束，整体路由传播覆盖仍未回到起点水平；窗口之后是继续回升、再次下降还是保持不变，当前快照无法回答。'
        : `更准确的说法是最低点至窗口结束${lowestToEndDirection}，窗口结束相对起点${endRelativeToStart}。窗口之后是继续上升、再次下降还是保持不变，当前快照无法回答。`
    const startToLowestAssessment =
      loss.value > 0
        ? '从起点到最低点出现明显且持续的可见性下降'
        : loss.value < 0
          ? '从起点到最低点出现可见性上升'
          : '从起点到最低点的可见性保持持平'
    const nonFinalUnknown = facts.snapshot.isFinal
      ? '现有证据不能回答观测窗口之后是否已经完全恢复'
      : '现有证据不能回答观测窗口之外或数据截止点之后的状态，也不能确认后续是否已经完全恢复'

    const fullyInvisibleMax = extremaPoint(
      facts.metricExtrema,
      'fully_invisible_asn_count',
      'max',
    )
    const partiallyVisibleMax = extremaPoint(
      facts.metricExtrema,
      'partially_visible_asn_count',
      'max',
    )
    const ipv4RatioMin = extremaPoint(
      facts.metricExtrema,
      'ipv4_visible_prefix_vp_ratio',
      'min',
    )
    const ipv6RatioMin = extremaPoint(
      facts.metricExtrema,
      'ipv6_visible_prefix_vp_ratio',
      'min',
    )
    const updateMax = extremaPoint(
      facts.resourceMetricExtrema,
      'update_total',
      'max',
    )
    const announceMax = extremaPoint(
      facts.resourceMetricExtrema,
      'announce_count',
      'max',
    )
    const withdrawMax = extremaPoint(
      facts.resourceMetricExtrema,
      'withdraw_count',
      'max',
    )
    const ipv4ResourceMax = extremaPoint(
      facts.resourceMetricExtrema,
      'ipv4_24_equivalent_count',
      'max',
    )
    const ipv4ResourceMin = extremaPoint(
      facts.resourceMetricExtrema,
      'ipv4_24_equivalent_count',
      'min',
    )
    const ipv4ResourceChange = facts.derivedFacts.find(
      (item) => item.metric === 'ipv4_24_equivalent_max_to_min_change',
    )

    const highlights = [
      {
        label: '固定观测范围',
        value: `${formatInteger(facts.cohort.origin_asn_count)} 个 origin ASN`,
        evidenceRefs: ['overview:/cohort/origin_asn_count'],
      },
      {
        label: '固定路由观测关系',
        value: `${formatInteger(facts.cohort.prefix_vp_count)} 条 Prefix×VP`,
        evidenceRefs: ['overview:/cohort/prefix_vp_count'],
      },
      {
        label: '窗口起点覆盖率',
        value: formatPercent(start.visiblePrefixVpRatio),
        evidenceRefs: [startRef],
      },
      {
        label: '窗口最低覆盖率',
        value: formatPercent(lowest.visiblePrefixVpRatio),
        evidenceRefs: [minRef],
      },
      {
        label: '窗口结束覆盖率',
        value: formatPercent(end.visiblePrefixVpRatio),
        evidenceRefs: [endRef],
      },
    ]
    if (largestDrop) {
      highlights.push({
        label:
          largestDropDelta === undefined || largestDropDelta < 0
            ? '最大单槽下降'
            : largestDropDelta > 0
              ? '最小单槽上升'
              : '单槽最小变化',
        value: `${formatInteger(
          Math.abs(largestDropDelta ?? 0),
        )} 条 / 5 分钟`,
        evidenceRefs: [
          largestDrop.provenance.endpoint +
            ':' +
            largestDrop.provenance.pointer,
        ],
      })
    }
    if (fullyInvisibleMax) {
      highlights.push({
        label: '全不可见 ASN 峰值',
        value: `${formatInteger(fullyInvisibleMax.value)} 个`,
        evidenceRefs: ['series:/metric_extrema/fully_invisible_asn_count/max'],
      })
    }
    if (updateMax) {
      highlights.push({
        label: 'UPDATE 活动峰值',
        value: `${formatInteger(updateMax.value)} 条 / 5 分钟`,
        evidenceRefs: ['series:/resource_metric_extrema/update_total/max'],
      })
    }

    const sections: ReportSection[] = [
      {
        id: 'scope',
        title: '观测范围与证据边界',
        paragraphs: [
          paragraph(
            `${windowStart}至${windowEnd}，Domeye 通过 RRC25 对${country}相关 BGP 路由进行固定人口观测。固定范围包含 ${formatInteger(
              facts.cohort.origin_asn_count,
            )} 个 origin ASN 和 ${formatInteger(
              facts.cohort.prefix_vp_count,
            )} 条 Prefix×VP 关系。`,
            'overview:/observation_scope',
            'overview:/cohort',
          ),
          paragraph(
            'Prefix×VP 可以理解为某个前缀是否能从某个固定 BGP 观测点看到。同一前缀可能对应多个观测关系，因此它不是唯一前缀数，也不能直接换算成用户或业务数量。',
            'overview:/cohort/denominator_policy',
          ),
          paragraph(
            snapshotBoundaryParagraph,
            'overview:/observation_scope',
            'audit:/evidence_level',
          ),
        ],
      },
      {
        id: 'key_numbers',
        title: '最值得关注的数字',
        paragraphs: [
          paragraph(
            startToLowestKeyNumbers,
            startRef,
            minRef,
            endRef,
            loss.factId,
            lossRatio.factId,
          ),
        ],
      },
      {
        id: 'visibility',
        title:
          startToLowestDirection === '下降'
            ? '可见性是怎样下降的'
            : `起点至最低点可见性${startToLowestDirection}`,
        paragraphs: [
          paragraph(
            visibilityFirstParagraph,
            startRef,
            minRef,
          ),
          paragraph(
            visibilitySecondParagraph,
            loss.factId,
          ),
        ],
      },
    ]

    if (
      facts.capabilities.asn_matrix?.state === 'available' &&
      asnPages.length > 0
    ) {
      const topAsns = asnPages
        .flatMap((page) => page.items)
        .slice(0, 9)
        .flatMap((item) => {
          const asn =
            typeof item.asn === 'number'
              ? item.asn
              : typeof item.asn === 'string' && /^\d+$/.test(item.asn)
                ? Number(item.asn)
                : undefined
          const slots =
            typeof item.longest_fully_invisible_slots === 'number'
              ? item.longest_fully_invisible_slots
              : undefined
          const intervalSeconds = facts.scope.interval_seconds
          return (
            asn === undefined ||
            slots === undefined ||
            typeof intervalSeconds !== 'number'
          )
            ? []
            : [
                `AS${asn}（${formatDurationMinutes(
                  (slots * intervalSeconds) / 60,
                )}）`,
              ]
        })
      const asnParagraphs = [
        fullyInvisibleMax && partiallyVisibleMax
          ? paragraph(
              `${localTimeLabel(
                partiallyVisibleMax.observed_at_local,
              )}，部分可见 ASN 达到 ${formatInteger(
                partiallyVisibleMax.value,
              )} 个；${localTimeLabel(
                fullyInvisibleMax.observed_at_local,
              )}，全不可见 ASN 达到 ${formatInteger(
                fullyInvisibleMax.value,
              )} 个。两个峰值发生在不同时间，不能直接相加。`,
              'series:/metric_extrema/partially_visible_asn_count/max',
              'series:/metric_extrema/fully_invisible_asn_count/max',
            )
          : paragraph(
              'ASN 能力可用，但当前报告没有足够的同口径峰值用于比较。',
              'overview:/capabilities/asn_matrix',
            ),
      ]
      if (topAsns.length > 0) {
        asnParagraphs.push(
          paragraph(
            `按最长连续全不可见时间排序，页面前列包括 ${topAsns.join(
              '、',
            )}。持续时间不能直接理解为实际影响规模或责任排名。`,
            ...topAsns.map((_, index) => `asns:/items/${index}`),
          ),
        )
      }
      sections.push({
        id: 'asn_scope',
        title: '影响扩展到了多少网络',
        paragraphs: asnParagraphs,
      })
    }

    if (
      facts.capabilities.address_families?.state === 'available' &&
      ipv4RatioMin &&
      ipv6RatioMin
    ) {
      sections.push({
        id: 'address_families',
        title: 'IPv4 受到的变化更明显',
        paragraphs: [
          paragraph(
            `IPv4 最低覆盖率为 ${formatPercent(
              ipv4RatioMin.value,
              3,
            )}，IPv6 最低覆盖率为 ${formatPercent(
              ipv6RatioMin.value,
              3,
            )}。从同地址族固定人口的覆盖率看，本窗口变化主要体现在 IPv4。`,
            'series:/metric_extrema/ipv4_visible_prefix_vp_ratio/min',
            'series:/metric_extrema/ipv6_visible_prefix_vp_ratio/min',
          ),
          paragraph(
            '这不表示 IPv4 用户受到同比例影响。控制面路由数量和实际用户流量之间没有直接换算关系。',
            'overview:/limitations',
          ),
        ],
      })
    }

    if (
      facts.capabilities.update_activity?.state === 'available' &&
      updateMax &&
      announceMax &&
      withdrawMax &&
      largestDrop &&
      largestDropDelta !== undefined
    ) {
      const updateVisibilityDirection =
        largestDropDelta < 0
          ? '下降'
          : largestDropDelta > 0
            ? '上升'
            : '持平'
      sections.push({
        id: 'updates',
        title: `大规模 BGP 更新活动与${updateVisibilityDirection}发生在同一阶段`,
        paragraphs: [
          paragraph(
            `${localTimeLabel(
              updateMax.observed_at_local,
            )}，国家归属路由的 UPDATE 总量达到 ${formatInteger(
              updateMax.value,
            )} 条，其中 ANNOUNCE ${formatInteger(
              announceMax.value,
            )} 条、WITHDRAW ${formatInteger(
              withdrawMax.value,
            )} 条。${localTimeLabel(
              largestDrop.observedAtLocal,
            )}出现窗口${
              largestDropDelta < 0
                ? '最大单槽可见性下降'
                : largestDropDelta > 0
                  ? '最小单槽可见性上升'
                  : '单槽可见性持平'
            }。`,
            'series:/resource_metric_extrema/update_total/max',
            'series:/resource_metric_extrema/announce_count/max',
            'series:/resource_metric_extrema/withdraw_count/max',
            largestDrop.provenance.endpoint +
              ':' +
              largestDrop.provenance.pointer,
          ),
          paragraph(
            largestDropDelta < 0
              ? '两者时间上紧密相邻，是值得关注的信号，但现有证据不足以证明 UPDATE 峰值导致可见性下降。'
              : '两者发生在同一阶段，是值得关注的信号，但现有证据不足以证明 UPDATE 峰值导致同阶段的可见性变化。',
            'audit:/evidence_level',
          ),
        ],
      })
    }

    sections.push({
      id: 'end_state',
      title:
        recovered.value > 0
          ? '窗口后段出现回升，但还不能称为恢复'
          : recovered.value < 0
            ? '最低点至窗口结束继续下降'
            : '最低点至窗口结束持平',
      paragraphs: [
        paragraph(
          `${lowestToEndSentence}${endComparedWithStartSentence}`,
          minRef,
          endRef,
          recovered.factId,
          ...(recoveryUsesShare ? [recoveryShare.factId] : []),
          endGap.factId,
        ),
        paragraph(
          endStateSecondParagraph,
          endRef,
          'overview:/observation_scope/window_end_utc',
        ),
      ],
    })

    if (
      facts.capabilities.country_resources?.state === 'available' &&
      ipv4ResourceMax &&
      ipv4ResourceMin &&
      ipv4ResourceChange
    ) {
      sections.push({
        id: 'resources',
        title: '国家级路由资源也出现波动',
        paragraphs: [
          paragraph(
            `IPv4 /24 等价资源从窗口最大 ${formatInteger(
              ipv4ResourceMax.value,
            )} 个下降到最低 ${formatInteger(
              ipv4ResourceMin.value,
            )} 个，相差 ${formatInteger(
              ipv4ResourceChange.value,
            )} 个。`,
            'series:/resource_metric_extrema/ipv4_24_equivalent_count/max',
            'series:/resource_metric_extrema/ipv4_24_equivalent_count/min',
            ipv4ResourceChange.factId,
          ),
          paragraph(
            '这些数字表示规范化、去重后的路由资源覆盖，不是实际在线 IP 地址，也不是受影响用户数量。',
            'overview:/limitations',
          ),
        ],
      })
    }

    sections.push({
      id: 'assessment',
      title: '综合判断',
      paragraphs: [
        paragraph(
          `仅依据该固定快照，可以判断 RRC25 观察到${country}相关 BGP 路由${startToLowestAssessment}，${
            fullyInvisibleMax
              ? '变化覆盖多个 origin ASN，'
              : '当前快照不提供可核对的 ASN 影响范围，'
          }${lowestToEndSummary}，${endRelativeSummary}。`,
          startRef,
          minRef,
          endRef,
          ...(
            fullyInvisibleMax
              ? ['series:/metric_extrema/fully_invisible_asn_count/max']
              : []
          ),
        ),
        paragraph(
          '这份数据足以支持“BGP 控制面发生大范围可见性变化”，但不足以判断全国性互联网中断、用户和业务影响、事件原因、责任主体以及窗口之后是否完全恢复。',
          'audit:/evidence_level',
          'overview:/limitations',
        ),
        ...trendClaimParagraphs,
      ],
    })

    return {
      schemaVersion: 'country_outage_report_draft_v1',
      title: `${country} BGP 路由可见性观测报告`,
      subtitle: `${
        loss.value > 0
          ? '窗口内路由可见性明显下降'
          : loss.value < 0
            ? '起点至最低点路由可见性上升'
            : '起点至最低点路由可见性持平'
      }，${
        endGap.value > 0
          ? '结束时仍未回到起点水平'
          : endGap.value < 0
            ? '结束时高于起点'
            : '结束时与起点持平'
      }`,
      summary: paragraph(
        loss.value > 0
          ? `${windowStart}至${windowEnd}，RRC25 观察到${country}相关 BGP 路由可见性显著下降。最低覆盖率由起点 ${formatPercent(
              start.visiblePrefixVpRatio,
            )} 降至 ${formatPercent(
              lowest.visiblePrefixVpRatio,
            )}；${lowestToEndSummary}，${endRelativeSummary}。`
          : loss.value < 0
            ? `${windowStart}至${windowEnd}，RRC25 观察到${country}相关 BGP 路由起点至最低点可见性上升。覆盖率由起点 ${formatPercent(
                start.visiblePrefixVpRatio,
              )} 升至最低点 ${formatPercent(
                lowest.visiblePrefixVpRatio,
              )}；${lowestToEndSummary}，${endRelativeSummary}。`
            : `${windowStart}至${windowEnd}，RRC25 观察到${country}相关 BGP 路由起点至最低点可见性持平，覆盖率均为 ${formatPercent(
                start.visiblePrefixVpRatio,
              )}；${lowestToEndSummary}，${endRelativeSummary}。`,
        startRef,
        minRef,
        endRef,
      ),
      highlights,
      sections,
      unknowns: [
        '现有证据不能回答是否属于全国性互联网中断',
        '现有证据不能回答用户和具体业务受到多大影响',
        '现有证据不能回答事件由攻击、配置错误、政策行为还是基础设施故障引起',
        '现有证据不能回答哪个运营商或 ASN 应承担责任',
        nonFinalUnknown,
      ],
    }
}

export class DeterministicAcceptanceNarrator implements ReportNarrator {
  readonly identity = {
    provider: 'domeye',
    model: 'deterministic-acceptance-narrator',
    modelVersion: '1',
    adapter: 'deterministic-acceptance' as const,
  }
  readonly validatorRulesVersion =
    COUNTRY_OUTAGE_REPORT_VALIDATOR_RULES_VERSION
  readonly skillBundleSha256: string

  constructor(
    skillPath: string = defaultCountryOutageSkillPath(),
  ) {
    this.skillBundleSha256 =
      computeCountryOutageSkillBundleSha256(skillPath)
  }

  async generate(
    request: NarrationRequest,
  ): Promise<CountryOutageReportDraft> {
    return buildDeterministicCountryOutageDraft(request.evidence)
  }
}

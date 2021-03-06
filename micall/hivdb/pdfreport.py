#!/usr/bin/env python3.4

# The module that generates a report in PDF format
import pytz
import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.units import cm, mm
from reportlab.lib.styles import ParagraphStyle

import reportlab.platypus as plat

# we currently only support North American letter paper -- no A4
page_w, page_h = letter

# Times are reported in this time zone
TIME_ZONE_NAME = "America/Vancouver"
time_zone = pytz.timezone(TIME_ZONE_NAME)

# text size in the table in points
TAB_FONT_SIZE = 8

# text size in the small print
SMALL_PRINT_FONT_SIZE = 7

# a style used for the 'relevant mutations' text
mut_txt_style = ParagraphStyle("sconormal",
                               fontSize=TAB_FONT_SIZE,
                               leading=TAB_FONT_SIZE,
                               fontName='Helvetica-Oblique')


def get_now_string():
    """Return the date and time in the configured time zone as a string"""
    utc_now = datetime.datetime.now(tz=pytz.utc)
    loc_now = utc_now.astimezone(time_zone)
    # return loc_now.strftime('%Y-%b-%d %H:%M:%S %Z')
    return loc_now.strftime('%Y-%b-%d (%Z)')


def bottom_para(txt):
    "Set the provided text into a form for the small print"
    small_style = ParagraphStyle("small",
                                 fontSize=SMALL_PRINT_FONT_SIZE,
                                 leading=SMALL_PRINT_FONT_SIZE-1)
    return plat.Paragraph(txt, small_style)


def test_details_para(txt):
    "Set the provided text into a form for the test details"
    small_style = ParagraphStyle("small",
                                 fontSize=TAB_FONT_SIZE,
                                 leading=TAB_FONT_SIZE-1)
    return plat.Paragraph(txt, small_style)


def headertab_style(row_offset, colnum, dospan):
    """Generate a style list for the first row of a table with colnum columns.
    dospan := turn the colnum columns into a single one with centred text

    This routine is responsible for the style in the table headings.
    """
    lst = [("TEXTCOLOR", (0, row_offset), (colnum-1, row_offset), colors.white),
           ("BACKGROUND", (0, row_offset), (colnum-1, row_offset), colors.green),
           ("ALIGN", (0, row_offset), (colnum-1, row_offset), "CENTRE"),
           ("FACE", (0, row_offset), (colnum-1, row_offset), "Helvetica-Bold")]
    if dospan:
        lst.extend([("SPAN", (0, row_offset), (colnum-1, row_offset))])
        # ("BOX", (0, row_offset), (colnum-1, row_offset), 1, colors.black)])
    else:
        lst.extend([("GRID", (0, row_offset), (colnum-1, row_offset), 0.5, colors.black)])
    return lst


def drug_class_tablst(row_offset, cfg_dct, dc_name, level_coltab, compact=False):
    drug_lst = cfg_dct["known_drugs"][dc_name]
    table_header_str = cfg_dct['drug_class_tableheaders'][dc_name]
    resistance_dct = cfg_dct["res_results"]
    mutation_str = cfg_dct["mutation_strings"][dc_name]
    # 1) row 0: header column: name of drug_class
    t_data = [["{} Drugs".format(table_header_str), ""]]
    t_style = headertab_style(row_offset, 2, dospan=True)
    # 2) row 1..num_drugs: list of drugs in this drug_class
    drow_min, drow_max = row_offset + 1,  row_offset + len(drug_lst)
    t_style.append(("GRID", (0, drow_min), (1, drow_max), 1.0, colors.white))
    t_style.extend([("ALIGNMENT", (0, drow_min), (0, drow_max), 'LEFT'),
                    ("LEFTPADDING", (0, drow_min), (0, drow_max), 0),
                    ("ALIGNMENT", (1, drow_min), (1, drow_max), 'CENTRE'),
                    ("FACE", (1, drow_min), (1, drow_max), "Helvetica-Bold")])
    for tabline, dd in enumerate(drug_lst):
        drug_id, drug_name = dd
        if drug_id in resistance_dct:
            level, level_name = resistance_dct[drug_id]
        else:
            level, level_name = 1, "NOT REPORTED"
        t_data.append([drug_name.capitalize(), level_name])
        # determine colours for the level
        bg_col, fg_col = level_coltab[level]
        t_style.extend([('TEXTCOLOR', (1, tabline + drow_min), (1, tabline + drow_min), fg_col),
                        ('BACKGROUND', (1, tabline + drow_min), (1, tabline + drow_min), bg_col)])
        # if compact and tabline % 2 == 0:
        #    t_style.append(('BACKGROUND', (0, tabline + drow_min), (0, tabline + drow_min), colors.lightgrey))
    # 3) mutation string
    # we put this into a separate paragraph into a column that spans the two table columns
    mut_row = drow_max + 1
    t_style.extend([("SPAN", (0, mut_row), (1, mut_row)),
                    ("LEFTPADDING", (0, mut_row), (1, mut_row), 0)])
    # ("BOX", (0, mut_row), (1, mut_row), 0.5, colors.black)])
    t_data.append([plat.Paragraph(mutation_str, mut_txt_style), ""])
    assert sum([len(row) == 2 for row in t_data]) == len(t_data), "wonky drug table"
    return t_data, t_style


def drug_class_table(cfg_dct, dc_name, level_coltab, tabwidth):
    """Generate a resistance report for a given drug class.
    tabwidth: the total width allocated for the table.
    """
    # NOTE: this fudge factor ensures that the left, drug_name column, is not too wide.
    t_data, t_style = drug_class_tablst(0, cfg_dct, dc_name, level_coltab)
    colw = tabwidth * 0.36
    return plat.Table(t_data, vAlign="TOP", style=t_style, colWidths=[colw, None])


def top_table(sample_name, table_width):
    """Generate a (mostly empty) top table of three main columns.
    table_width: the overall width of the table.
    """
    samp_name = sample_name or "None"
    mid_colwidth = table_width/2.8
    oth_colwidth = (table_width - mid_colwidth)/2.0
    nowstr = get_now_string()
    test_dl = [["Patient/Sample Details", "Test Details", "Physician Details"],
               ["", test_details_para("Sample ID: {}".format(samp_name)), ""],
               ["", test_details_para("Report Date: {}".format(nowstr)), ""],
               ["", "", ""],
               ["", "", ""]
               ]
    rn_min, rn_max = 1, len(test_dl) - 1
    lc, mc, rc = 0, 1, 2
    st_lst = headertab_style(0, 3, dospan=False)
    st_lst.extend([("BOX", (lc, rn_min), (lc, rn_max), 0.5, colors.black),
                   ("BOX", (rc, rn_min), (rc, rn_max), 0.5, colors.black),
                   # ("GRID", (mc, rn_min), (mc, rn_max), 0.5, colors.black),
                   ("FONTSIZE", (lc, rn_min), (rc, rn_max), 8)])
    return plat.Table(test_dl, style=st_lst,
                      colWidths=[oth_colwidth, mid_colwidth, oth_colwidth],
                      hAlign="CENTRE")


def write_report_two_columns(cfg_dct, res_lst, mut_lst, fname, sample_name=None):
    """Generate a PDF report to a given output file name
    """
    col_tab = cfg_dct["resistance_level_colours"]
    level_coltab = dict([(k, (colors.HexColor(v[1]), colors.HexColor(v[2])))
                         for k, v in col_tab.items()])
    doc = plat.SimpleDocTemplate(
        fname,
        pagesize=letter,
        title="basespace HIV drug resistance genotype report",
        author="BCCfE in HIV/AIDS")
    # get the actual text width, (not the page width):
    txt_w = page_w - doc.leftMargin - doc.rightMargin
    w_half, top_table_col_width = txt_w * 0.5, txt_w / 3.3333
    doc_els = [plat.Spacer(1, 1.5 * cm)]
    ti_style = ParagraphStyle("scotitle", alignment=TA_CENTER, fontSize=20)
    doc_els.append(plat.Paragraph(cfg_dct["report_title"], ti_style))
    re_style = ParagraphStyle("scored", fontSize=15, textColor=colors.red,
                              spaceBefore=5 * mm, spaceAfter=5 * mm)
    doc_els.append(plat.Paragraph("For research use only", re_style))
    # -- top table
    doc_els.append(top_table(sample_name, top_table_col_width))
    lc, rc = 0, 1
    big_table, btstyle = [], []
    # now drug classes tables, two per line
    known_dc_lst = cfg_dct["known_dclass_list"]
    # from the resistance, we determine which drug_classes to write a table for:
    # we only write a table if we are given resistance data for it.
    got_dc_set = set([dc["drug_class"] for dc in res_lst])
    tl = [drug_class_table(cfg_dct, dc, level_coltab, w_half) for dc in known_dc_lst if dc in got_dc_set]
    d_rowmin = 0
    while len(tl) > 0:
        row_lst = [tl.pop(0)]
        if len(tl) > 0:
            row_lst.append(tl.pop(0))
        else:
            row_lst.append("")
        big_table.append(row_lst)
    d_rowmax = len(big_table) - 1
    btstyle.extend([
        ("ALIGN", (lc, d_rowmin), (lc, d_rowmax), "RIGHT"),
        ("ALIGN", (rc, d_rowmin), (rc, d_rowmax), "LEFT"),
        ('VALIGN', (lc, d_rowmin), (rc, d_rowmax), 'TOP')])
    # this is for layout debugging
    # big_table = [["l0", "r0"], ["l1", "r1"], ["l2", "r2"]]
    # debug_lst = [("GRID", (lc, 0), (rc, d_rowmax), 1, colors.red)]
    # btstyle.extend(debug_lst)
    assert sum(len(row) == 2 for row in big_table) == len(big_table), "big_table lines are wonky"
    doc_els.append(plat.Table(big_table,
                              style=btstyle,
                              colWidths=[w_half, w_half],
                              hAlign="CENTRE"))
    # bottom paragraphs
    doc_els.append(bottom_para(cfg_dct["disclaimer_text"]))
    doc_els.append(bottom_para(cfg_dct["generated_by_text"]))
    doc.build(doc_els)


def write_report_one_column(cfg_dct, res_lst, mut_lst, fname, sample_name=None):
    """Generate a PDF report to a given output file name
    """
    col_tab = cfg_dct["resistance_level_colours"]
    level_coltab = dict([(k, (colors.HexColor(v[1]), colors.HexColor(v[2])))
                         for k, v in col_tab.items()])
    doc = plat.SimpleDocTemplate(
        fname,
        pagesize=letter,
        topMargin=1 * cm,
        title="basespace drug resistance report",
        author="BCCfE")
    # get the actual text width, (not the page width):
    txt_w = page_w - doc.leftMargin - doc.rightMargin
    table_width = txt_w - 1 * cm
    doc_els = []
    ti_style = ParagraphStyle("scotitle", alignment=TA_CENTER, fontSize=20)
    doc_els.append(plat.Paragraph(cfg_dct["report_title"], ti_style))
    re_style = ParagraphStyle("scored", fontSize=15, textColor=colors.red,
                              spaceBefore=5 * mm, spaceAfter=5 * mm)
    doc_els.append(plat.Paragraph("For research use only", re_style))
    # -- top table
    doc_els.append(top_table(sample_name, table_width))
    # now drug classes tables, two per line
    known_dc_lst = cfg_dct["known_dclass_list"]
    # from the resistance, we determine which drug_classes to write a table for:
    # we only write a table if we are given resistance data for it.
    got_dc_set = set([dc["drug_class"] for dc in res_lst])
    tot_tab, tot_style = [], []
    for dc in [dc for dc in known_dc_lst if dc in got_dc_set]:
        tl, t_style = drug_class_tablst(len(tot_tab), cfg_dct, dc, level_coltab, compact=True)
        tot_tab.extend(tl)
        tot_style.extend(t_style)
    # adjust the overall table style
    num_rows = len(tot_tab)
    tot_style.extend([("VALIGN", (0, 0), (1, num_rows-1), "TOP"),
                      ("FONTSIZE", (0, 0), (1, num_rows-1), TAB_FONT_SIZE),
                      ("LEADING", (0, 0), (1, num_rows-1), TAB_FONT_SIZE)])
    left_col_w = table_width * 0.36
    right_col_w = table_width - left_col_w
    doc_els.append(plat.Table(tot_tab,
                              vAlign="TOP",
                              hAlign="CENTRE", style=tot_style,
                              colWidths=[left_col_w, right_col_w]))
    # this is for layout debugging
    # big_table = [["l0", "r0"], ["l1", "r1"], ["l2", "r2"]]
    # debug_lst = [("GRID", (lc, 0), (rc, d_rowmax), 1, colors.red)]
    # btstyle.extend(debug_lst)
    # bottom paragraphs
    doc_els.append(bottom_para(cfg_dct["disclaimer_text"]))
    doc_els.append(bottom_para(cfg_dct["generated_by_text"]))
    doc.build(doc_els)


def gen_testpage(fname):
    write_report_two_columns({}, [], [], fname)


def simple_gen_testpage(fname):
    """Generate a simple test page"""
    # NOTE: this example taken from
    # https://www.blog.pythonlibrary.org/2010/09/21/reportlab-tables-creating-tables-in-pdfs-with-python/
    doc = plat.SimpleDocTemplate(fname, pagesize=letter)
    elements = []
    data = [['00', '01', '02', '03', '04'], ['10', '11', '12', '13', '14'],
            ['20', '21', '22', '23', '24'], ['30', '31', '32', '33', '34']]
    t = plat.Table(data)
    t.setStyle(
        plat.TableStyle([('BACKGROUND', (1, 1), (-2, -2), colors.green), (
            'TEXTCOLOR', (0, 0), (1, -1), colors.red)]))
    elements.append(t)
    # write the document to disk
    doc.build(elements)


if __name__ == '__main__':
    print("The local time is '{}'".format(get_now_string()))
    # gen_testpage("testpage.pdf")

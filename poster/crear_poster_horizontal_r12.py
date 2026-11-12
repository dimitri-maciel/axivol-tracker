"""Horizontal poster, 120 x 90 cm, with expanded pilot evidence, evaluation plan and compact references."""
from pathlib import Path
import argparse,json,math
from urllib.parse import urlsplit
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing, STATE_DEFAULTS
from reportlab.graphics import renderPDF
from xml.sax.saxutils import escape
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.colors import HexColor
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph
parser = argparse.ArgumentParser(description='Póster horizontal con avances ampliados, referencias compactas y QR de Axivol Tracker.')
parser.add_argument('--repo-url', default='https://github.com/dimitri-maciel/axivol-tracker', help='Dirección HTTPS verificada del repositorio de la aplicación.')
args = parser.parse_args()
if args.repo_url:
 url = urlsplit(args.repo_url)
 if url.scheme != 'https' or not url.hostname or not url.path.strip('/') or url.username or url.password:
  parser.error('--repo-url debe ser una dirección HTTPS del repositorio, sin credenciales.')
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'output/pdf'
A=ROOT/'poster/assets/evidencia_r2';A4=ROOT/'poster/assets/evidencia_v4'
AR=ROOT/'poster/assets/evidencia_r2'
E=json.loads((A4/'procedencia.json').read_text())
for n,f in [('Sans','Arial.ttf'),('Bold','Arial Bold.ttf'),('Italic','Arial Italic.ttf')]:pdfmetrics.registerFont(TTFont(n,'/System/Library/Fonts/Supplemental/'+f))
pdfmetrics.registerFontFamily('Sans',normal='Sans',bold='Bold',italic='Italic')
# Keep the vector renderer on the same embedded font as the poster.
STATE_DEFAULTS['fontName']='Sans'
W,H=120*72/2.54,90*72/2.54;M=108;FW=W-M*2;G=72;CW=(FW-2*G)/3;RX=M+CW+G;TX=RX+CW+G
WHITE=HexColor('#FFFFFF');INK=HexColor('#171717');WINE=HexColor('#861B3B')
c=canvas.Canvas(str(OUT/'Poster_Tesis_Dimitri_Maciel_Horizontal_r12.pdf'),pagesize=(W,H),initialFontName='Sans')
c.setTitle('Seguimiento volumétrico del carcinoma hepatocelular');c.setAuthor('Dimitri Maciel')
def box(x,y,w,h,color=INK):
 c.setFillColor(color);c.rect(x,H-y-h,w,h,stroke=0,fill=1)
def tint(x,y,w,h,color=INK,alpha=.06):
 c.saveState();c.setFillColor(color);c.setFillAlpha(alpha)
 c.rect(x,H-y-h,w,h,stroke=0,fill=1);c.restoreState()
def ln(x,y,xx,yy,col=INK,lw=2):
 c.setStrokeColor(col);c.setLineWidth(lw);c.line(x,H-y,xx,H-yy)
def txt(s,x,y,fs=29,font='Sans',col=INK):
 assert x+pdfmetrics.stringWidth(s,font,fs)<=W-55,(s,x,fs)
 c.setFillColor(col);c.setFont(font,fs);c.drawString(x,H-y-fs,s)
def p(s,x,y,w,fs=29,col=INK,bottom=None,lead=None):
 a=Paragraph(s,ParagraphStyle('p',fontName='Sans',fontSize=fs,leading=lead or fs*1.24,textColor=col))
 _,h=a.wrap(w,9000);assert bottom is None or y+h<=bottom,(s[:80],y+h,bottom)
 a.drawOn(c,x,H-y-h);return y+h
def circle(x,y,r,col=WINE,fill=1):
 c.setStrokeColor(col);c.setFillColor(col);c.setLineWidth(3);c.circle(x,H-y,r,stroke=not fill,fill=fill)
def img(file,x,y,w,h):c.drawImage(str(file),x,H-y-h,w,h,preserveAspectRatio=True,anchor='c',mask='auto')
def arrow(x,y,xx,yy,col=INK,lw=3.5,head=13):
 a=math.atan2(yy-y,xx-x)
 # Shaft stops at the head base: no protruding line or doubled tip.
 bx=xx-head*math.cos(a);by=yy-head*math.sin(a)
 ln(x,y,bx,by,col,lw)
 q=c.beginPath();q.moveTo(xx,H-yy)
 for side in [-1,1]:
  q.lineTo(bx+side*head*.48*math.sin(a),H-(by-side*head*.48*math.cos(a)))
 q.close();c.setFillColor(col);c.drawPath(q,fill=1,stroke=0)
def centered(s,cx,y,fs=29,font='Sans',col=INK):
 txt(s,cx-pdfmetrics.stringWidth(s,font,fs)/2,y,fs,font,col)
def section(n,s,x,y,col=INK):
 txt(n,x,y+4,29,'Bold',WINE if col==INK else WHITE);txt(s,x+60,y,41,'Bold',col)
def stroke_rect(x,y,w,h,col=INK,lw=2):
 c.setStrokeColor(col);c.setLineWidth(lw);c.rect(x,H-y-h,w,h,stroke=1,fill=0)
def caption(n,s,x,y,w,light=False,bottom=None):
 color=WHITE if light else INK
 p('<b>Fig. '+str(n)+'.</b>',x,y,94,22,WHITE if light else WINE,lead=28)
 return p(s,x+102,y,w-102,22,color,bottom=bottom,lead=28)

# The landscape width supports a complete title on one line.
centered('UNIVERSIDAD AUTÓNOMA DE GUADALAJARA',W/2,46,27,'Bold',WINE)
centered('Seguimiento volumétrico del carcinoma hepatocelular',W/2,109,94,'Bold')
centered('Antes y después de la quimioembolización transarterial (TACE)',W/2,228,36,'Sans',WINE)
txt('Dimitri Maciel',M,292,30,'Bold')
centered('Maestría en Ciencias Computacionales',W/2,294,28)
director='Director de tesis: por asignar'
txt(director,W-M-pdfmetrics.stringWidth(director,'Sans',26),296,26)
ln(M,351,W-M,351,WINE,2)

# Three introductory columns, with matched headers and bottom baselines.
tint(0,376,W,602,INK,.035)
for x in [RX-G/2,TX-G/2]:ln(x,392,x,956,HexColor('#D0D0D0'),1.5)
section('01','Problema y brecha',M,390)
p('Comparar tomografías computarizadas (TC) antes y después de TACE exige localizar la misma lesión (Fig. 1).',M,457,CW,29,bottom=563,lead=34)
labels=[('Posición','Respiración: desplazamiento del hígado.'),('Contraste','Fase: cambios en el realce.'),('Resolución','Espaciado: variación del muestreo.')]
for i,(head,desc) in enumerate(labels):
 yy=588+i*80;cx=M+83;tx=M+230
 if i==0:
  stroke_rect(cx-63,yy-27,72,60,INK,3);stroke_rect(cx-23,yy-9,72,60,WINE,3)
  arrow(cx-44,yy-42,cx+46,yy-42,WINE,4)
 elif i==1:
  circle(cx-37,yy+8,34,INK);circle(cx+42,yy+8,34,WINE)
  circle(cx-37,yy+8,12,WHITE);circle(cx+42,yy+8,12,WHITE,0)
 else:
  for j in range(5):ln(cx-75,yy-30+j*18,cx-10,yy-30+j*18,INK,3)
  for j in range(3):ln(cx+17,yy-30+j*36,cx+82,yy-30+j*36,WINE,5)
 ln(M+178,yy+7,tx-22,yy+7,HexColor('#959595'),2);circle(tx-22,yy+7,3,INK)
 txt(head,tx,yy-35,30,'Bold');p(desc,tx,yy+5,CW-230,27,bottom=yy+46,lead=32)
 if i<2:ln(tx,yy+51,M+CW,yy+51,HexColor('#D5D5D5'),1)
box(M,829,5,62,WINE)
p('<b>Brecha.</b> HCC-TACE-Seg: máscaras PRE sin referencia POST equivalente [1]. Falta evaluar comparabilidad y error de medición.',M+24,829,CW-24,27,bottom=898,lead=31)
ln(M,905,M+CW,905,HexColor('#BDBDBD'),1)
caption(1,'Factores de comparabilidad. Elaboración propia.',M,922,CW,bottom=978)

section('02','Pregunta y objetivos',RX,390)
p('¿Qué precisión ofrece la comparación volumétrica antes y después de TACE al combinar registro hepático y correspondencia de lesiones?',RX,457,CW,30,bottom=568,lead=35)
img(A/'higado_lesion_3d.png',RX+3,615,450,270)
txt('Lesión objetivo',RX+8,575,26,'Bold',WINE)
ln(RX+49,613,RX+112,613,WINE,2.5);ln(RX+112,613,RX+150,733,WINE,2.5)
circle(RX+150,733,6,WHITE);circle(RX+150,733,3,WINE)
txt('Hígado',RX+318,862,26,'Bold')
ln(RX+341,850,RX+378,850,INK,2.5);ln(RX+341,850,RX+327,763,INK,2.5);circle(RX+327,763,5,INK)
txt('Objetivo general',RX+503,575,30,'Bold')
p('Desarrollar y evaluar una aplicación de volumetría longitudinal (Fig. 2).',RX+503,622,CW-503,29,bottom=727,lead=34)
ln(RX+518,762,RX+518,878,HexColor('#B8B8B8'),2)
for i,s in enumerate(['Verificar pares de estudios.','Evaluar registro y segmentación.','Cuantificar errores de medición.']):
 yy=745+i*58;circle(RX+518,yy+17,17,INK);centered(str(i+1),RX+518,yy+1,24,'Bold',WHITE)
 p(s,RX+553,yy,CW-553,27,bottom=yy+59,lead=30)
ln(RX,905,RX+CW,905,HexColor('#BDBDBD'),1)
caption(2,'HCC_002: Liver y Mass. Superficies suavizadas; medición sobre la máscara original.',RX,922,CW,bottom=978)

section('03','Estado del arte',TX,390)
colx=[TX,TX+216,TX+585]; widths=[190,344,CW-585]
for x,s in zip(colx,['Trabajo','Aportación','Límite']):txt(s,x,461,26,'Bold',WINE)
rows=[('Moawad [1]<br/>2023','TC y máscaras PRE.','Sin referencia POST equivalente.'),('Hering [2]<br/>2024','Registro y segmentación.','Evaluado en melanoma.'),('Rokuss [3]<br/>2025','LesionLocator:<br/>seguimiento 3D.','Por evaluar en HCC-TACE.')]
for i,row in enumerate(rows):
 yy=505+i*110
 if i%2==0:tint(TX-10,yy-13,CW+20,109,INK,.035)
 ln(TX,yy-16,TX+CW,yy-16,HexColor('#BBBBBB'),1)
 for k,s in enumerate(row):p('<b>'+s+'</b>' if k==0 else s,colx[k],yy+19,widths[k],27,bottom=yy+105,lead=32)
ln(TX,835,TX+CW,835,INK,1.5)
box(TX,856,5,110,WINE)
txt('Aportación propuesta',TX+24,856,29,'Bold',WINE)
p('Verificar pares comparables, conservar su procedencia y señalar mediciones que requieren revisión.',TX+24,900,CW-24,28,bottom=978,lead=33)

# Figure 3: instructions under the headings, broad visual scenes below.
# Image and direct annotations share the horizontal space of each stage.
BY=998
box(M,BY,FW,82,INK)
section('04','Propuesta metodológica',M+40,BY+19,WHITE)
txt('Fig. 3',W-M-124,BY+27,26,'Bold',WHITE)
stepw=(FW-96-300)/4;sx=[M+48+i*(stepw+100) for i in range(4)]
descs=['Verificar fase, cobertura y geometría de ambas TC.',
       'Revisar la alineación y la correspondencia anatómica.',
       'Revisar las máscaras PRE y POST de la misma lesión.',
       'Contar los vóxeles N con su espaciado original.']
for i,(x,head,desc) in enumerate(zip(sx,['Seleccionar','Alinear','Delimitar','Cuantificar'],descs)):
 circle(x+23,BY+122,23,WINE);centered(str(i+1),x+23,BY+106,26,'Bold',WHITE)
 txt(head,x+64,BY+99,38,'Bold')
 p(desc,x,BY+159,stepw,28,bottom=BY+194,lead=34)
 if i<3:
  gx=x+stepw+50
  ln(gx,BY+104,gx,BY+336,HexColor('#C6C6C6'),1.6)
  ln(gx,BY+414,gx,BY+554,HexColor('#C6C6C6'),1.6)
  arrow(gx-31,BY+375,gx+31,BY+375,INK,4.5,17)
# 1. The two source acquisitions occupy almost the full stage width.
fw=285;pairw=fw*2+28
for j,name in enumerate(['pre_contexto.png','post_contexto.png']):
 x=sx[0]+(stepw-pairw)/2+j*(fw+28)
 img(A4/name,x,BY+226,fw,fw)
 centered('Antes' if j==0 else 'Después',x+fw/2,BY+530,27,'Bold')
# 2. Real liver surface plus the three candidate registration models.
img(AR/'registro_3d.png',sx[1]+5,BY+218,410,337)
lx=sx[1]+444
ln(sx[1]+383,BY+359,lx-22,BY+359,INK,2)
ln(lx-22,BY+313,lx-22,BY+405,INK,2)
for label,yy in [('Rígido',BY+295),('Afín',BY+341),('Deformable',BY+387)]:
 ln(lx-22,yy+18,lx-8,yy+18,INK,2);txt(label,lx+4,yy,27)
# 3. Published PRE lesion with a direct annotation, not a POST output.
img(A/'lesion_3d.png',sx[2]+15,BY+219,350,337)
labelx=sx[2]+407
ln(sx[2]+296,BY+383,labelx-20,BY+383,WINE,2.5)
circle(sx[2]+296,BY+383,6,WHITE);circle(sx[2]+296,BY+383,3,WINE)
txt('Lesión objetivo',labelx,BY+330,27,'Bold')
txt('Máscara PRE',labelx,BY+377,25,col=WINE)
# 4. The enlarged voxel and its calculation are side by side.
x=sx[3]+75;y=BY+290;ss=140;dx=75;dy=52
def face(points,color,alpha=1):
 c.saveState();c.setFillColor(color);c.setFillAlpha(alpha)
 q=c.beginPath();q.moveTo(points[0][0],H-points[0][1])
 for xx,yy in points[1:]:q.lineTo(xx,H-yy)
 q.close();c.drawPath(q,fill=1,stroke=0);c.restoreState()
top=[(x,y),(x+ss,y),(x+ss+dx,y-dy),(x+dx,y-dy)]
front=[(x,y),(x+ss,y),(x+ss,y+ss),(x,y+ss)]
side=[(x+ss,y),(x+ss+dx,y-dy),(x+ss+dx,y+ss-dy),(x+ss,y+ss)]
face(top,WINE,.82);face(front,WINE,.18);face(side,INK,.18)
for points in [top,front,side]:
 for a,b in zip(points,points[1:]+points[:1]):ln(*a,*b,INK,3)
centered('Δx',x+ss/2,y+ss+17,27,'Bold');txt('Δz',x-48,y+ss/2-17,27,'Bold');txt('Δy',x+ss+dx+13,y-dy+25,27,'Bold')
numerator='N × Δx × Δy × Δz';nw=pdfmetrics.stringWidth(numerator,'Bold',28);nx=sx[3]+426
txt('V =',sx[3]+350,BY+337,30,'Bold')
txt(numerator,nx,BY+316,28,'Bold')
ln(nx-8,BY+358,nx+nw+8,BY+358,INK,1.7);centered('1000',nx+nw/2,BY+372,26,'Bold')
centered('Δ en mm; V en cm³',nx+nw/2,BY+433,25)
# Figure 3 caption: one quiet baseline, source at left, status at right.
# Stage geometry is retained; the caption separates illustrative inputs from pending work.
ln(M+40,BY+582,W-M-40,BY+582,HexColor('#C6C6C6'),1.2)
cut=M+FW*.62
p('<b>Fig. 3.</b>',M+40,BY+610,100,24,WINE,lead=30)
p('Caso HCC_002: tomografías antes y después de TACE y máscara previa al tratamiento.',M+154,BY+610,cut-M-184,24,bottom=BY+641,lead=30)
ln(cut,BY+602,cut,BY+645,HexColor('#D0D0D0'),1.2)
p('<font color="#861B3B"><b>Por desarrollar:</b></font> registro y segmentación posterior.',cut+32,BY+610,W-M-40-cut-32,24,bottom=BY+641,lead=30)

# Pilot evidence: collection inventory, implemented scope and one measured case.
PW=2*CW+G
section('05','Avances técnicos',M,1675)
txt('105',M+730,1675,43,'Bold');txt('casos disponibles',M+840,1691,25)
txt('677',M+1270,1675,43,'Bold');txt('series DICOM',M+1380,1691,25)
txt('1 caso piloto',M+PW-190,1691,25,'Bold',WINE)
box(M,1732,PW,53,INK)
txt('Implementado',M+22,1744,27,'Bold',WHITE)
ln(M+224,1743,M+224,1773,HexColor('#9A9A9A'),1.2)
txt('Visor de TC · Superposición de máscara · Volumetría PRE',M+250,1744,27,'Sans',WHITE)
ln(TX-G/2,1679,TX-G/2,2226,HexColor('#D0D0D0'),1.5)

# The CT is 47% larger than R11; the area plot is 62% taller.
txt('A  TC PRE y máscara',M,1800,27,'Bold')
img(A/'tc_mascara.png',M,1840,350,350)
cx=M+480;cy=1870;cw=890;ch=235
txt('B  Área de la máscara por corte',cx,1800,27,'Bold')
arrow(M+378,2001,M+425,2001,INK,4)
for v in [0,40,80]:
 yy=cy+ch*(1-v/80);txt(str(v),cx-40,yy-12,22);ln(cx,yy,cx+cw,yy,HexColor('#B8B8B8'),1)
ln(cx,cy,cx,cy+ch,INK,1);ln(cx,cy+ch,cx+cw,cy+ch,INK,1);txt('cm²',cx-45,cy-34,22)
q=c.beginPath();q.moveTo(cx,H-cy-ch)
for i,a in enumerate(E['area_cm2']):q.lineTo(cx+i*cw/108,H-(cy+ch*(1-a/80)))
q.lineTo(cx+cw,H-cy-ch);q.close();c.setFillColor(WINE);c.drawPath(q,fill=1,stroke=0)
slice_i=75;px=cx+slice_i*cw/108;py=cy+ch*(1-E['area_cm2'][slice_i]/80)
ln(px,py-23,px,cy+ch,INK,2);circle(px,py,6,WHITE);circle(px,py,3,INK);centered('Corte 76',px,py-53,23,'Bold')
for i in [0,54,108]:centered(str(i+1),cx+i*cw/108,cy+ch+10,22)
centered('Número de corte',cx+cw/2,2153,24)
arrow(cx+cw+35,2001,cx+cw+125,2001,WINE,3.5,13)
vx=M+1540
txt('495,04 cm³',vx,1880,66,'Bold',WINE)
p('Volumen de la máscara PRE',vx,1972,PW-1540,29,bottom=2042,lead=35)
ln(vx,2049,M+PW,2049,HexColor('#C8C8C8'),1)
txt('324 430 vóxeles',vx,2072,27,'Bold')
txt('109 cortes vinculados',vx,2115,27)
caption(4,'HCC_002, adquisición 1, etiqueta Mass. Corte 76 de 109. Medición de la máscara publicada; no indica viabilidad.',M,2206,PW,bottom=2235)

# Planned evaluation is explicitly separated from implemented capabilities.
section('06','Trabajo futuro',TX,1675)
box(TX,1732,CW,53,WINE)
txt('Por desarrollar',TX+22,1744,27,'Bold',WHITE)
txt('Evaluación longitudinal',TX+310,1744,27,'Sans',WHITE)
future=[('Verificar pares','Fase, cobertura y correspondencia.'),
        ('Evaluar métodos','Registro y referencia de segmentación POST.'),
        ('Evaluar mediciones','Error volumétrico, tiempo y comparaciones descartadas.')]
for i,(head,sub) in enumerate(future):
 yy=1817+i*117
 circle(TX+18,yy+18,18,WINE);centered(str(i+1),TX+18,yy+3,22,'Bold',WHITE)
 txt(head,TX+64,yy,29,'Bold');p(sub,TX+64,yy+41,CW-64,27,bottom=yy+105,lead=32)
 if i<2:arrow(TX+18,yy+43,TX+18,yy+117-15,INK,2.8,11)
p('Comparación PRE y POST aún pendiente.',TX+64,2173,CW-64,25,WINE,bottom=2204,lead=31)

# Three complete, compact IEEE entries; DOI links are attached to their titles.
ln(M,2246,W-M,2246,HexColor('#BBBBBB'),1.5)
txt('Referencias clave',M,2258,37,'Bold')
refs=[
 ('1','A. W. Moawad et al., “Multimodality annotated hepatocellular carcinoma data set including pre- and post-TACE with imaging segmentation,” <i>Scientific Data</i>, vol. 10, art. 33, 2023.','https://doi.org/10.1038/s41597-023-01928-3'),
 ('2','A. Hering et al., “Improving assessment of lesions in longitudinal CT scans: a bi-institutional reader study on an AI-assisted registration and volumetric segmentation workflow,” <i>IJCARS</i>, vol. 19, pp. 1689-1697, 2024.','https://doi.org/10.1007/s11548-024-03181-4'),
 ('3','M. Rokuss et al., “LesionLocator: Zero-Shot Universal Tumor Segmentation and Tracking in 3D Whole-Body Imaging,” <i>CVPR</i>, pp. 30872-30885, 2025.','https://doi.org/10.1109/CVPR52734.2025.02875')]
refgap=38;refwidth=(PW-2*refgap)/3
for i,(number,body,doi) in enumerate(refs):
 x=M+i*(refwidth+refgap)
 p('<b>['+number+']</b>',x,2308,35,25,WINE,lead=27)
 p('<a href="'+doi+'" color="#171717">'+body+'</a>',x+42,2308,refwidth-42,25,bottom=H-M,lead=27)

# Repository access occupies its own footer column; scientific image credit stays visible.
if args.repo_url:
 qr_size=184;qr_x=W-M-qr_size;qr_y=2255
 widget=QrCodeWidget(args.repo_url,barLevel='M',barBorder=4,barFillColor=HexColor('#000000'))
 x0,y0,x1,y1=widget.getBounds()
 qr=Drawing(qr_size,qr_size,transform=[qr_size/(x1-x0),0,0,qr_size/(y1-y0),-x0,-y0]);qr.add(widget)
 renderPDF.draw(qr,c,qr_x,H-qr_y-qr_size)
 c.linkURL(args.repo_url,(qr_x,H-qr_y-qr_size,qr_x+qr_size,H-qr_y),relative=0,thickness=0)
 label_x=TX;label_width=qr_x-label_x-42
 p('<b>Axivol Tracker</b>',label_x,2258,label_width,37,bottom=2302,lead=44)
 p('Repositorio del proyecto',label_x,2310,label_width,25,bottom=2342,lead=31)
 shown=args.repo_url.removeprefix('https://').rstrip('/')
 p('<a href="'+escape(args.repo_url,{'"':'&quot;'})+'" color="#861B3B">'+escape(shown)+'</a>',label_x,2350,label_width,23,WINE,bottom=2380,lead=29)
 p('Imágenes: <a href="https://doi.org/10.7937/TCIA.5FNA-0924" color="#171717">Moawad et al., HCC-TACE-Seg (TCIA).</a> <a href="https://creativecommons.org/licenses/by/4.0/" color="#171717">CC BY 4.0.</a>',label_x,2397,label_width,20,bottom=H-M,lead=23)
c.showPage();c.save();print(OUT/'Poster_Tesis_Dimitri_Maciel_Horizontal_r12.pdf')

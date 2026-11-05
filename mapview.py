"""Static toolbox map assets, projected onto the same north-up scale as radar dots."""
import json
import math
from pathlib import Path


ASSETS=Path(__file__).resolve().parent/'map_assets'
BACKGROUND=(15,23,42)


def world_to_image(config,x,y):
    """Toolbox formula: u=(x-ox)/side, v=(y-oy)/side; +Y points down."""
    return ((x-config['origin_x'])/config['side_units']*config['declared_width'],
            (y-config['origin_y'])/config['side_units']*config['declared_height'])


def image_transform(config,player,radius_m,width,height,padding):
    """Pillow inverse transform: canvas pixel -> source pixel, no oversized bitmap."""
    ring_radius=max(1,min(width,height)/2-padding)
    source_x,source_y=world_to_image(config,player['x'],player['y'])
    units_per_screen_pixel=100*radius_m/ring_radius
    a=units_per_screen_pixel*config['declared_width']/config['side_units']
    e=units_per_screen_pixel*config['declared_height']/config['side_units']
    return (a,0,source_x-a*width/2,0,e,source_y-e*height/2)


class MapRenderer:
    def __init__(self,assets=ASSETS):
        self.assets=Path(assets);self.maps={};self.error=''
        self.images={};self.failures={};self.frame_key=None;self.frame=None
        self.paint_key=None;self.paint_mask=None;self.paint_count=0
        self.tint_key=None;self.tinted=None
        try:
            self.maps=json.loads((self.assets/'maps.json').read_text(encoding='utf-8'))['maps']
        except (OSError,ValueError,KeyError,TypeError):self.error='地图配置缺失'

    def render(self,scene,player,radius_m,width,height,padding):
        """Return (PIL image or None, short status). Failures preserve ordinary radar."""
        config=self.maps.get(str(scene))
        if self.error:return None,self.error
        if not config:return None,'等待场景' if not scene else f'场景 {scene} 无底图'
        name=config['name']
        if not config.get('image'):return None,f'{name}缺底图'
        if not player:return None,'等待玩家坐标'
        try:
            x,y=player['x'],player['y']
            if not all(math.isfinite(v) for v in (x,y,radius_m)) or radius_m<=0:
                return None,'地图坐标无效'
            side=config['side_units']
            if not (config['origin_x']<=x<=config['origin_x']+side and
                    config['origin_y']<=y<=config['origin_y']+side):
                return None,'玩家坐标超出底图'
        except (KeyError,TypeError,ValueError):return None,'地图坐标无效'
        key=str(scene)
        if key in self.failures:return None,self.failures[key]
        try:
            from PIL import Image
            if key not in self.images:
                path=(self.assets/config['image']).resolve()
                if path.parent!=self.assets.resolve():raise ValueError('invalid map path')
                with Image.open(path) as source:
                    expected=(config['declared_width'],config['declared_height'])
                    if source.size!=expected:raise ValueError('map dimensions do not match calibration')
                    self.images[key]=source.convert('RGB')
            cache_key=(key,x,y,radius_m,width,height,padding)
            if cache_key!=self.frame_key:
                transform=image_transform(config,player,radius_m,width,height,padding)
                if not all(math.isfinite(v) for v in transform):return None,'地图比例无效'
                self.frame=self.images[key].transform((width,height),Image.Transform.AFFINE,
                    transform,resample=Image.Resampling.BICUBIC,fillcolor=BACKGROUND)
                self.frame_key=cache_key
            return self.frame,name
        except ImportError:
            self.failures[key]='地图需要 Pillow，请运行安装依赖.bat 后重启'
        except (OSError,ValueError,OverflowError):
            self.failures[key]='地图图片无法读取或尺寸不符'
        return None,self.failures[key]

    def coverage_mask(self,scene,exploration):
        from PIL import Image,ImageDraw
        config=self.maps[str(scene)];points=exploration.get('points',())
        key=(exploration.get('scope'),str(scene),exploration.get('token'),config['declared_width'],config['declared_height'])
        if key!=self.paint_key or len(points)<self.paint_count:
            self.paint_mask=Image.new('L',(config['declared_width'],config['declared_height']),0)
            self.paint_key=key;self.paint_count=0;self.tint_key=None
        if len(points)>self.paint_count:
            draw=ImageDraw.Draw(self.paint_mask)
            rx=10000*config['declared_width']/config['side_units']
            ry=10000*config['declared_height']/config['side_units']
            for x,y in points[self.paint_count:]:
                ix,iy=world_to_image(config,x,y)
                draw.ellipse((ix-rx,iy-ry,ix+rx,iy+ry),fill=72)
            self.paint_count=len(points);self.tint_key=None
        return self.paint_mask

    def paint(self,frame,scene,player,radius_m,width,height,padding,exploration,opacity=112):
        """Union of observed 100m disks; opacity is display-only and never changes stored data."""
        from PIL import Image
        self.coverage_mask(scene,exploration)
        # A source frame changes whenever view center, scale or window size changes.
        tint_key=(self.frame_key,self.paint_key,self.paint_count,opacity)
        if tint_key!=self.tint_key:
            transform=image_transform(self.maps[str(scene)],player,radius_m,width,height,padding)
            alpha=self.paint_mask.transform((width,height),Image.Transform.AFFINE,transform,
                resample=Image.Resampling.NEAREST,fillcolor=0)
            alpha=alpha.point([min(255,round(v/72*opacity)) for v in range(256)])
            self.tinted=Image.composite(Image.new('RGB',(width,height),(124,58,237)),frame,alpha)
            self.tint_key=tint_key
        return self.tinted

import time
import argparse
from torch.autograd import Variable
import torch.backends.cudnn as cudnn
from tqdm import tqdm
from utils import *
from common import * 
import torch

from model_HLFSR import HLFSR

# Settings
def parse_args():
	parser = argparse.ArgumentParser()
	parser.add_argument('--device', type=str, default='cuda:0')
	parser.add_argument("--angRes", type=int, default=5, help="angular resolution")
	parser.add_argument("--upscale_factor", type=int, default=4, help="upscale factor")
	parser.add_argument('--model_name', type=str, default='HLFSR')
	parser.add_argument('--trainset_dir', type=str, default='F:/1.Data/1.Vinh/2. Research/1. Research skku/5.LFSR/4.HLFSR/Data_HLFSR/x4/TrainingData_5x5_4xSR')
	parser.add_argument('--testset_dir', type=str, default='F:/1.Data/1.Vinh/2. Research/1. Research skku/5.LFSR/4.HLFSR/Data_HLFSR/x4/TestData_4xSR_5x5_Small/')


	parser.add_argument('--batch_size', type=int, default=2)
	parser.add_argument('--lr', type=float, default=2e-4, help='initial learning rate')
	parser.add_argument('--n_epochs', type=int, default=50, help='number of epochs to train')
	parser.add_argument('--n_steps', type=int, default=15, help='number of epochs to update learning rate')
	parser.add_argument('--gamma', type=float, default=0.5, help='learning rate decaying factor')

	parser.add_argument("--patchsize", type=int, default=64, help="crop into patches for validation")
	parser.add_argument("--stride", type=int, default=32, help="stride for patch cropping")

	parser.add_argument('--load_pretrain', type=bool, default=False)
	parser.add_argument('--model_path', type=str, default='./log/HLFSR_4xSR_5x5_epoch_24.pth.tar')

	parser.add_argument("--n_groups", type=int, default=5, help="number of HLFSR-Groups")
	parser.add_argument("--n_blocks", type=int, default=15, help="number of HLFSR-Blocks")
	parser.add_argument("--channels", type=int, default=64, help="number of channels")
	parser.add_argument("--n_GPUs", type=int, default=2, help="number of GPUs")
	parser.add_argument("--crop_test",type=bool, default=True)

	return parser.parse_args()


def train(cfg, train_loader, test_Names, test_loaders):

	net = HLFSR(angRes=cfg.angRes, n_blocks=cfg.n_blocks,
				   channels=cfg.channels, upscale_factor=cfg.upscale_factor)
	# net.apply(weights_init_xavier)

	net.to(cfg.device)
	cudnn.benchmark = True
	epoch_state = 0

	# print(net)
	total_params = sum(p.numel() for p in net.parameters())
	print("Total Params: {:.2f}".format(total_params)) 

	if cfg.load_pretrain:
		if os.path.isfile(cfg.model_path):
			model = torch.load(cfg.model_path, map_location={'cuda:0,1': cfg.device})
			net.load_state_dict(model['state_dict'])
			epoch_state = model["epoch"]
		else:
			print("=> no model found at '{}'".format(cfg.load_model))

	# net = torch.nn.DataParallel(net, device_ids=[0, 1])

	criterion_Loss = torch.nn.L1Loss().to(cfg.device)
	optimizer = torch.optim.Adam([paras for paras in net.parameters() if paras.requires_grad == True], lr=cfg.lr)
	scheduler = torch.optim.lr_scheduler.StepLR(optimizer, step_size=cfg.n_steps, gamma=cfg.gamma)
	scheduler._step_count = epoch_state
	loss_epoch = []
	loss_list = []

	for idx_epoch in range(epoch_state, cfg.n_epochs):
		for idx_iter, (data, label) in tqdm(enumerate(train_loader), total=len(train_loader)):
			data, label = Variable(data).to(cfg.device), Variable(label).to(cfg.device)
			out = net(data)
			loss = criterion_Loss(out, label)
			optimizer.zero_grad()
			loss.backward()
			optimizer.step()
			loss_epoch.append(loss.data.cpu())

		if idx_epoch % 1 == 0:
			loss_list.append(float(np.array(loss_epoch).mean()))
			print(time.ctime()[4:-5] + ' Epoch----%5d, loss---%f' % (idx_epoch + 1, float(np.array(loss_epoch).mean())))
			save_ckpt({
				'epoch': idx_epoch + 1,
				'state_dict': net.state_dict(),
				# 'state_dict': net.module.state_dict(),  # for torch.nn.DataParallel
				'loss': loss_list,},
				save_path='./log/', filename=cfg.model_name + '_' + str(cfg.upscale_factor) + 'xSR_' + str(cfg.angRes) +
							'x' + str(cfg.angRes) + '_epoch_' + str(idx_epoch + 1) + '.pth.tar')
			loss_epoch = []

		''' evaluation '''
		with torch.no_grad():
			psnr_testset = []
			ssim_testset = []
			for index, test_name in enumerate(test_Names):
				test_loader = test_loaders[index]
				psnr_epoch_test, ssim_epoch_test = valid(test_loader, net,angRes=cfg.angRes,n_GPUs=cfg.n_GPUs)
				psnr_testset.append(psnr_epoch_test)
				ssim_testset.append(ssim_epoch_test)
				print(time.ctime()[4:-5] + ' Valid----%15s, PSNR---%f, SSIM---%f' % (test_name, psnr_epoch_test, ssim_epoch_test))
				pass
			pass

		scheduler.step()
		pass


def valid(test_loader, net, angRes=5,n_GPUs=2):
	psnr_iter_test = []
	ssim_iter_test = []
	for idx_iter, (data, label) in (enumerate(test_loader)):
		data = data.to(cfg.device)  # numU, numV, h*angRes, w*angRes
		label = label.squeeze()

		b, c, h, w = data.size()
		if cfg.crop_test:
			scale  = cfg.upscale_factor
			h_half, w_half = h // 2, w // 2
			h_size, w_size = h_half , w_half 

			lr_list = [
				data[:, :, 0:h_size, 0:w_size],
				data[:, :, 0:h_size, (w - w_size):w],
				data[:, :, (h - h_size):h, 0:w_size],
				data[:, :, (h - h_size):h, (w - w_size):w]]


			sr_list = []
			# n_GPUs = 1
			for i in range(0, 4, n_GPUs):
				lr_batch = torch.cat(lr_list[i:(i + n_GPUs)], dim=0)
				with torch.no_grad():
					sr_batch = net(lr_batch)
				sr_batch = SAI2MacPI(sr_batch,angRes)
				sr_list.extend(sr_batch.chunk(n_GPUs, dim=0))


			h, w = scale * h, scale * w
			h_half, w_half = scale * h_half, scale * w_half
			h_size, w_size = scale * h_size, scale * w_size
		


			outLF = data.new(b, c, h, w)
			outLF[:, :, 0:h_half, 0:w_half] \
				= sr_list[0][:, :, 0:h_half, 0:w_half]
			outLF[:, :, 0:h_half, w_half:w] \
				= sr_list[1][:, :, 0:h_half, (w_size - w + w_half):w_size]
			outLF[:, :, h_half:h, 0:w_half] \
				= sr_list[2][:, :, (h_size - h + h_half):h_size, 0:w_half]
			outLF[:, :, h_half:h, w_half:w] \
				= sr_list[3][:, :, (h_size - h + h_half):h_size, (w_size - w + w_half):w_size]

			# img_tmp = outLF.squeeze()
			# imgplot = plt.imshow(img_tmp.cpu().numpy())
			# plt.show()

			outLF = MacPI2SAI(outLF,angRes)

			# img_tmp = outLF.squeeze()
			# imgplot = plt.imshow(img_tmp.cpu().numpy())
			# plt.show()

		else: 
			with torch.no_grad():
				outLF = net(data)

		outLF = outLF.squeeze()

		#covert SAI to 4DLF
		outLF = SAI24DLF(outLF, cfg.angRes)
		label = SAI24DLF(label, cfg.angRes)

		psnr, ssim = cal_metrics(label, outLF, cfg.angRes)

		psnr_iter_test.append(psnr)
		ssim_iter_test.append(ssim)
		pass

	psnr_epoch_test = float(np.array(psnr_iter_test).mean())
	ssim_epoch_test = float(np.array(ssim_iter_test).mean())

	return psnr_epoch_test, ssim_epoch_test


def save_ckpt(state, save_path='./log', filename='checkpoint.pth.tar'):
	torch.save(state, os.path.join(save_path,filename))
def weights_init_xavier(m):
    classname = m.__class__.__name__
    if classname.find('Conv2d') != -1:
        torch.nn.init.xavier_normal_(m.weight.data)

def main(cfg):
	train_set = TrainSetLoader(dataset_dir=cfg.trainset_dir)
	train_loader = DataLoader(dataset=train_set, num_workers=12, batch_size=cfg.batch_size, shuffle=True)
	test_Names, test_Loaders, length_of_tests = MultiTestSetDataLoader(cfg)
	train(cfg, train_loader, test_Names, test_Loaders)


if __name__ == '__main__':
	cfg = parse_args()
	main(cfg)
